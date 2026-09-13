`timescale 1ns/1ps

module cpu_core #(
    parameter integer DMEM_BYTES = 4096,
    parameter integer ENABLE_BPU = 1
)(
    input  wire        clk,
    input  wire        rst_n,

    output wire        ibus_req_valid,
    input  wire        ibus_req_ready,
    output wire [31:0] ibus_req_addr,
    input  wire        ibus_resp_valid,
    output wire        ibus_resp_ready,
    input  wire [31:0] ibus_resp_rdata,

    output wire        dbus_req_valid,
    input  wire        dbus_req_ready,
    output wire [31:0] dbus_req_addr,
    output wire [31:0] dbus_req_wdata,
    output wire [3:0]  dbus_req_wstrb,
    input  wire        dbus_resp_valid,
    output wire        dbus_resp_ready,
    input  wire [31:0] dbus_resp_rdata,

    // Verification-only flow-control hook. It never changes ISA semantics.
    input  wire        fetch_enable,
    // Testbench-only drain hook asserted after the configured completion
    // instruction has retired. It discards only younger speculative work.
    input  wire        verification_halt,

    // Stable retirement observation interface. trace_valid is registered so
    // a checker can sample it after the active clock edge without an NBA race.
    output reg         trace_valid,
    output reg  [31:0] trace_epoch,
    output reg  [63:0] trace_event_order,
    output reg  [63:0] trace_retire_order,
    output reg  [63:0] trace_cycle,
    output reg  [63:0] trace_uid,
    output reg         trace_trap,
    output reg  [31:0] trace_pc,
    output reg  [31:0] trace_instr,
    output reg  [31:0] trace_next_pc,
    output reg         trace_rd_we,
    output reg  [4:0]  trace_rd,
    output reg  [31:0] trace_rd_data,
    output reg         trace_mem_valid,
    output reg         trace_mem_write,
    output reg  [31:0] trace_mem_addr,
    output reg  [1:0]  trace_mem_size,
    output reg  [31:0] trace_store_data,
    output reg  [31:0] trace_mem_wdata,
    output reg  [3:0]  trace_mem_wstrb,
    output reg  [31:0] trace_mem_raw,
    output reg  [31:0] trace_mem_rdata,
    output reg  [31:0] trace_cause,
    output reg  [31:0] trace_epc,

    output wire        core_idle,

    // Event-level coverage/debug signals. These are observations only.
    output wire [1:0]  dbg_forward_a,
    output wire [1:0]  dbg_forward_b,
    output wire        dbg_forward_blocked,
    output wire        dbg_load_hazard,
    output wire        dbg_wb_id_bypass_rs,
    output wire        dbg_wb_id_bypass_rt,
    output wire        dbg_redirect,
    output wire        dbg_mispredict,
    output wire        dbg_branch_resolve,
    output wire        dbg_fetch_discard,
    output wire        dbg_exmem_wait
);
    localparam [31:0] RESET_PC = 32'h0000_0000;
    localparam [31:0] TRAP_VEC = 32'h0000_0080;
    localparam [31:0] CSR_BASE = 32'hFFFF_F000;

    localparam [1:0] MSIZE_W = 2'b00;
    localparam [1:0] MSIZE_H = 2'b01;
    localparam [1:0] MSIZE_B = 2'b10;

    localparam [31:0] CAUSE_MISALIGNED_LOAD  = 32'd1;
    localparam [31:0] CAUSE_MISALIGNED_STORE = 32'd2;
    localparam [31:0] CAUSE_UNMAPPED_LOAD    = 32'd3;
    localparam [31:0] CAUSE_UNMAPPED_STORE   = 32'd4;
    localparam [31:0] CAUSE_CSR_ACCESS       = 32'd5;
    localparam [31:0] CAUSE_ILLEGAL          = 32'd6;

    function csr_addr_valid;
        input [31:0] addr;
        begin
            case (addr)
                32'hFFFF_F000, 32'hFFFF_F004, 32'hFFFF_F008,
                32'hFFFF_F00C, 32'hFFFF_F010, 32'hFFFF_F0F0,
                32'hFFFF_F0F4: csr_addr_valid = 1'b1;
                default:       csr_addr_valid = 1'b0;
            endcase
        end
    endfunction

    function [31:0] load_extend;
        input [31:0] raw;
        input [1:0]  size;
        input [1:0]  offset;
        input        is_signed;
        reg [7:0] byte_value;
        reg [15:0] half_value;
        begin
            case (size)
                MSIZE_B: begin
                    case (offset)
                        2'd0: byte_value = raw[7:0];
                        2'd1: byte_value = raw[15:8];
                        2'd2: byte_value = raw[23:16];
                        default: byte_value = raw[31:24];
                    endcase
                    load_extend = is_signed ? {{24{byte_value[7]}}, byte_value}
                                            : {24'b0, byte_value};
                end
                MSIZE_H: begin
                    half_value = offset[1] ? raw[31:16] : raw[15:0];
                    load_extend = is_signed ? {{16{half_value[15]}}, half_value}
                                            : {16'b0, half_value};
                end
                default: load_extend = raw;
            endcase
        end
    endfunction

    function [31:0] size_bytes;
        input [1:0] size;
        begin
            case (size)
                MSIZE_B: size_bytes = 32'd1;
                MSIZE_H: size_bytes = 32'd2;
                default: size_bytes = 32'd4;
            endcase
        end
    endfunction

    // ---------------------------------------------------------------------
    // Fetch request owner and response buffer
    // ---------------------------------------------------------------------
    reg [31:0] fetch_pc;
    reg [63:0] next_fetch_uid;

    wire bpu_pred_taken_raw;
    wire bpu_pred_hit_raw;
    wire [31:0] bpu_pred_target_raw;

    reg        bpu_update_en;
    reg [31:0] bpu_update_pc;
    reg        bpu_update_taken;
    reg [31:0] bpu_update_target;

    bpu #(.BHT_ENTRIES(64), .BTB_ENTRIES(32)) u_bpu (
        .clk(clk),
        .rst_n(rst_n),
        .if_pc(fetch_pc),
        .pred_taken(bpu_pred_taken_raw),
        .pred_target(bpu_pred_target_raw),
        .pred_hit(bpu_pred_hit_raw),
        .update_en(bpu_update_en),
        .update_pc(bpu_update_pc),
        .update_taken(bpu_update_taken),
        .update_target(bpu_update_target)
    );

    wire launch_pred_taken = (ENABLE_BPU != 0) && bpu_pred_hit_raw && bpu_pred_taken_raw;
    wire [31:0] launch_pred_target = bpu_pred_target_raw;

    reg        if_req_present;
    reg [31:0] if_req_addr_reg;
    reg        if_req_stale;
    reg        if_req_pred_taken;
    reg [31:0] if_req_pred_target;
    reg [63:0] if_req_uid;

    reg        if_outstanding;
    reg        if_outstanding_stale;
    reg [31:0] if_outstanding_pc;
    reg        if_outstanding_pred_taken;
    reg [31:0] if_outstanding_pred_target;
    reg [63:0] if_outstanding_uid;

    reg        if_buf_valid;
    reg [31:0] if_buf_instr;
    reg [31:0] if_buf_pc;
    reg        if_buf_pred_taken;
    reg [31:0] if_buf_pred_target;
    reg [63:0] if_buf_uid;

    wire ibus_req_fire = ibus_req_valid && ibus_req_ready;
    wire ibus_resp_fire = ibus_resp_valid && ibus_resp_ready;

    // ---------------------------------------------------------------------
    // IF/ID stage and decode
    // ---------------------------------------------------------------------
    reg        IF_ID_valid;
    reg [31:0] IF_ID_pc;
    reg [31:0] IF_ID_instr;
    reg        IF_ID_pred_taken;
    reg [31:0] IF_ID_pred_target;
    reg [63:0] IF_ID_uid;

    wire [5:0] dec_opcode = IF_ID_instr[31:26];
    wire [4:0] dec_rs     = IF_ID_instr[25:21];
    wire [4:0] dec_rt     = IF_ID_instr[20:16];
    wire [4:0] dec_rd     = IF_ID_instr[15:11];
    wire [4:0] dec_shamt  = IF_ID_instr[10:6];
    wire [5:0] dec_funct  = IF_ID_instr[5:0];
    wire [15:0] dec_imm   = IF_ID_instr[15:0];

    wire dec_regDst;
    wire dec_aluSrc;
    wire dec_memToReg;
    wire dec_regWrite;
    wire dec_memRead;
    wire dec_memWrite;
    wire dec_branch;
    wire dec_branch_ne;
    wire [2:0] dec_aluOp;
    wire dec_jump;
    wire dec_jal;
    wire dec_immZeroExt;
    wire [1:0] dec_memSize;
    wire dec_loadSigned;
    wire dec_eret;
    wire dec_usesRs;
    wire dec_usesRt;
    wire dec_illegal_raw;

    control_unit u_ctrl (
        .opcode(dec_opcode), .funct(dec_funct),
        .regDst(dec_regDst), .aluSrc(dec_aluSrc),
        .memToReg(dec_memToReg), .regWrite(dec_regWrite),
        .memRead(dec_memRead), .memWrite(dec_memWrite),
        .branch(dec_branch), .branch_ne(dec_branch_ne),
        .aluOp(dec_aluOp), .jump(dec_jump), .jal(dec_jal),
        .immZeroExt(dec_immZeroExt), .memSize(dec_memSize),
        .loadSigned(dec_loadSigned), .eret(dec_eret),
        .usesRs(dec_usesRs), .usesRt(dec_usesRt),
        .illegal(dec_illegal_raw)
    );

    wire dec_illegal = dec_illegal_raw || (dec_eret && (IF_ID_instr[25:0] != 26'b0));
    wire [31:0] dec_imm_ext = dec_immZeroExt ? {16'b0, dec_imm}
                                                : {{16{dec_imm[15]}}, dec_imm};

    // ---------------------------------------------------------------------
    // ID/EX stage
    // ---------------------------------------------------------------------
    reg        ID_EX_valid;
    reg [31:0] ID_EX_pc;
    reg [31:0] ID_EX_instr;
    reg        ID_EX_pred_taken;
    reg [31:0] ID_EX_pred_target;
    reg [63:0] ID_EX_uid;
    reg [31:0] ID_EX_regData1;
    reg [31:0] ID_EX_regData2;
    reg [31:0] ID_EX_immExt;
    reg [4:0]  ID_EX_rs;
    reg [4:0]  ID_EX_rt;
    reg [4:0]  ID_EX_rd;
    reg [4:0]  ID_EX_shamt;
    reg [5:0]  ID_EX_funct;
    reg [5:0]  ID_EX_opcode;
    reg        ID_EX_usesRs;
    reg        ID_EX_usesRt;
    reg        ID_EX_illegal;
    reg        ID_EX_regWrite;
    reg        ID_EX_memRead;
    reg        ID_EX_memWrite;
    reg        ID_EX_memToReg;
    reg        ID_EX_branch;
    reg        ID_EX_branch_ne;
    reg        ID_EX_jump;
    reg        ID_EX_eret;
    reg [2:0]  ID_EX_aluOp;
    reg        ID_EX_aluSrc;
    reg        ID_EX_regDst;
    reg        ID_EX_jal;
    reg [1:0]  ID_EX_memSize;
    reg        ID_EX_loadSigned;

    // ---------------------------------------------------------------------
    // EX/MEM stage: owns an external transaction through its response.
    // ---------------------------------------------------------------------
    reg        EX_MEM_valid;
    reg [31:0] EX_MEM_pc;
    reg [31:0] EX_MEM_instr;
    reg [31:0] EX_MEM_next_pc;
    reg [63:0] EX_MEM_uid;
    reg        EX_MEM_trap;
    reg [31:0] EX_MEM_cause;
    reg [31:0] EX_MEM_epc;
    reg [31:0] EX_MEM_aluResult;
    reg [31:0] EX_MEM_storeData;
    reg [31:0] EX_MEM_storeAligned;
    reg [3:0]  EX_MEM_storeStrobe;
    reg [31:0] EX_MEM_pc_link;
    reg [4:0]  EX_MEM_regDest;
    reg        EX_MEM_regWrite;
    reg        EX_MEM_memRead;
    reg        EX_MEM_memWrite;
    reg        EX_MEM_memToReg;
    reg        EX_MEM_jal;
    reg [1:0]  EX_MEM_memSize;
    reg        EX_MEM_loadSigned;
    reg        EX_MEM_mem_accepted;

    // ---------------------------------------------------------------------
    // MEM/WB stage: always drains and retires at most once.
    // ---------------------------------------------------------------------
    reg        MEM_WB_valid;
    reg [31:0] MEM_WB_pc;
    reg [31:0] MEM_WB_instr;
    reg [31:0] MEM_WB_next_pc;
    reg [63:0] MEM_WB_uid;
    reg        MEM_WB_trap;
    reg [31:0] MEM_WB_cause;
    reg [31:0] MEM_WB_epc;
    reg [31:0] MEM_WB_result;
    reg [4:0]  MEM_WB_regDest;
    reg        MEM_WB_regWrite;
    reg        MEM_WB_memValid;
    reg        MEM_WB_memWrite;
    reg [31:0] MEM_WB_memAddr;
    reg [1:0]  MEM_WB_memSize;
    reg [31:0] MEM_WB_storeData;
    reg [31:0] MEM_WB_memWdata;
    reg [3:0]  MEM_WB_memWstrb;
    reg [31:0] MEM_WB_memRaw;
    reg [31:0] MEM_WB_memRdata;

    wire wb_commit_we = !verification_halt && MEM_WB_valid &&
                        !MEM_WB_trap && MEM_WB_regWrite &&
                        (MEM_WB_regDest != 5'd0);
    wire [31:0] rf_read1;
    wire [31:0] rf_read2;
    reg_file u_rf (
        .clk(clk), .rst_n(rst_n), .we(wb_commit_we),
        .ra1(dec_rs), .ra2(dec_rt), .wa(MEM_WB_regDest),
        .wd(MEM_WB_result), .rd1(rf_read1), .rd2(rf_read2)
    );

    wire wb_id_bypass_rs = IF_ID_valid && dec_usesRs && wb_commit_we &&
                           (MEM_WB_regDest == dec_rs);
    wire wb_id_bypass_rt = IF_ID_valid && dec_usesRt && wb_commit_we &&
                           (MEM_WB_regDest == dec_rt);
    wire [31:0] dec_regData1 = wb_id_bypass_rs ? MEM_WB_result : rf_read1;
    wire [31:0] dec_regData2 = wb_id_bypass_rt ? MEM_WB_result : rf_read2;

    // ---------------------------------------------------------------------
    // MEM completion and CSR state
    // ---------------------------------------------------------------------
    wire exmem_csr_page = ((EX_MEM_aluResult & 32'hFFFF_FF00) == CSR_BASE);
    wire exmem_csr_read = EX_MEM_valid && !EX_MEM_trap && EX_MEM_memRead &&
                          !EX_MEM_memWrite && exmem_csr_page;
    wire exmem_external_mem = EX_MEM_valid && !EX_MEM_trap &&
                              (EX_MEM_memRead || EX_MEM_memWrite) && !exmem_csr_page;

    assign dbus_req_valid = !verification_halt && exmem_external_mem &&
                            !EX_MEM_mem_accepted;
    assign dbus_req_addr  = EX_MEM_aluResult;
    assign dbus_req_wdata = EX_MEM_storeAligned;
    assign dbus_req_wstrb = EX_MEM_memWrite ? EX_MEM_storeStrobe : 4'b0000;
    wire dbus_req_fire = dbus_req_valid && dbus_req_ready;

    assign dbus_resp_ready = exmem_external_mem && EX_MEM_mem_accepted;
    wire dbus_resp_fire = dbus_resp_valid && dbus_resp_ready;

    wire [31:0] external_load_value = load_extend(
        dbus_resp_rdata, EX_MEM_memSize, EX_MEM_aluResult[1:0], EX_MEM_loadSigned
    );

    wire [31:0] csr_rdata;
    wire [31:0] csr_epc;
    wire [31:0] csr_cause;
    wire csr_trap_set;
    wire [31:0] csr_trap_epc;
    wire [31:0] csr_trap_cause;
    wire csr_retire_event;
    wire pipeline_stall_event;
    wire redirect_event;

    csr_perf u_csr (
        .clk(clk), .rst_n(rst_n),
        .stall_event(pipeline_stall_event), .flush_event(redirect_event),
        .retire_event(csr_retire_event), .trap_set(csr_trap_set),
        .epc_w(csr_trap_epc), .cause_w(csr_trap_cause),
        .addr(EX_MEM_aluResult), .rdata(csr_rdata),
        .epc_ro(csr_epc), .cause_ro(csr_cause)
    );

    wire [31:0] exmem_result_value = EX_MEM_jal ? EX_MEM_pc_link :
                                       exmem_csr_read ? csr_rdata :
                                       (EX_MEM_memRead ? external_load_value : EX_MEM_aluResult);
    wire exmem_result_ready = EX_MEM_valid && !EX_MEM_trap && EX_MEM_regWrite &&
                              (EX_MEM_jal || !EX_MEM_memRead || exmem_csr_read || dbus_resp_fire);
    wire exmem_complete = EX_MEM_valid &&
                          (EX_MEM_trap || exmem_csr_read ||
                           (!(EX_MEM_memRead || EX_MEM_memWrite)) ||
                           (exmem_external_mem && dbus_resp_fire));
    wire exmem_fire = exmem_complete;
    wire exmem_can_accept = !EX_MEM_valid || exmem_fire;

    // ---------------------------------------------------------------------
    // EX forwarding and datapath
    // ---------------------------------------------------------------------
    wire [1:0] forwardA;
    wire [1:0] forwardB;
    wire forward_blocked_a;
    wire forward_blocked_b;
    wire forward_blocked;
    forwarding_unit u_fwd (
        .EX_MEM_valid(EX_MEM_valid),
        .EX_MEM_regWrite(EX_MEM_regWrite && !EX_MEM_trap),
        .EX_MEM_resultReady(exmem_result_ready),
        .EX_MEM_rd(EX_MEM_regDest),
        .MEM_WB_valid(MEM_WB_valid),
        .MEM_WB_regWrite(MEM_WB_regWrite && !MEM_WB_trap),
        .MEM_WB_rd(MEM_WB_regDest),
        .ID_EX_valid(ID_EX_valid),
        .ID_EX_usesRs(ID_EX_usesRs), .ID_EX_usesRt(ID_EX_usesRt),
        .ID_EX_rs(ID_EX_rs), .ID_EX_rt(ID_EX_rt),
        .forwardA(forwardA), .forwardB(forwardB),
        .blockedA(forward_blocked_a), .blockedB(forward_blocked_b),
        .blocked(forward_blocked)
    );

    wire [31:0] fwdA = (forwardA == 2'b10) ? exmem_result_value :
                       (forwardA == 2'b01) ? MEM_WB_result : ID_EX_regData1;
    wire [31:0] fwdB = (forwardB == 2'b10) ? exmem_result_value :
                       (forwardB == 2'b01) ? MEM_WB_result : ID_EX_regData2;
    wire [31:0] alu_in2 = ID_EX_aluSrc ? ID_EX_immExt : fwdB;
    wire [31:0] alu_result;
    wire alu_zero;
    alu u_alu (
        .in1(fwdA), .in2(alu_in2), .shamt(ID_EX_shamt),
        .aluOp(ID_EX_aluOp), .funct(ID_EX_funct),
        .zero(alu_zero), .result(alu_result)
    );

    wire branch_equal = (fwdA == fwdB);
    wire branch_taken = ID_EX_branch && (ID_EX_branch_ne ? !branch_equal : branch_equal);
    wire [31:0] idex_pc_plus4 = ID_EX_pc + 32'd4;
    wire [31:0] branch_target = idex_pc_plus4 + (ID_EX_immExt << 2);
    wire [31:0] jump_target = {idex_pc_plus4[31:28], ID_EX_instr[25:0], 2'b00};
    wire [4:0] idex_reg_dest = ID_EX_jal ? 5'd31 :
                                      (ID_EX_regDst ? ID_EX_rd : ID_EX_rt);

    reg [3:0] ex_store_strobe;
    reg [31:0] ex_store_aligned;
    always @* begin
        ex_store_strobe = 4'b0000;
        ex_store_aligned = 32'b0;
        case (ID_EX_memSize)
            MSIZE_B: begin
                ex_store_strobe = 4'b0001 << alu_result[1:0];
                ex_store_aligned = {24'b0, fwdB[7:0]} << (8 * alu_result[1:0]);
            end
            MSIZE_H: begin
                ex_store_strobe = alu_result[1] ? 4'b1100 : 4'b0011;
                ex_store_aligned = {16'b0, fwdB[15:0]} << (16 * alu_result[1]);
            end
            default: begin
                ex_store_strobe = 4'b1111;
                ex_store_aligned = fwdB;
            end
        endcase
    end

    wire ex_mem_op = ID_EX_memRead || ID_EX_memWrite;
    wire ex_csr_page = ((alu_result & 32'hFFFF_FF00) == CSR_BASE);
    wire ex_word_misaligned = (ID_EX_memSize == MSIZE_W) && (alu_result[1:0] != 2'b00);
    wire ex_half_misaligned = (ID_EX_memSize == MSIZE_H) && alu_result[0];
    wire ex_misaligned = ex_mem_op && (ex_word_misaligned || ex_half_misaligned);
    wire [32:0] ex_access_last = {1'b0, alu_result} +
                                 {1'b0, size_bytes(ID_EX_memSize)} - 33'd1;
    wire ex_in_dmem = (alu_result < DMEM_BYTES) &&
                      (ex_access_last < {1'b0, DMEM_BYTES});
    wire ex_csr_bad = ex_mem_op && ex_csr_page &&
                      (!ID_EX_memRead || ID_EX_memWrite ||
                       (ID_EX_memSize != MSIZE_W) || !csr_addr_valid(alu_result));
    wire ex_unmapped = ex_mem_op && !ex_csr_page && !ex_in_dmem;
    wire ex_trap = ID_EX_illegal || ex_misaligned || ex_csr_bad || ex_unmapped;

    reg [31:0] ex_trap_cause;
    always @* begin
        if (ID_EX_illegal)
            ex_trap_cause = CAUSE_ILLEGAL;
        else if (ex_misaligned && ID_EX_memRead)
            ex_trap_cause = CAUSE_MISALIGNED_LOAD;
        else if (ex_misaligned && ID_EX_memWrite)
            ex_trap_cause = CAUSE_MISALIGNED_STORE;
        else if (ex_csr_bad)
            ex_trap_cause = CAUSE_CSR_ACCESS;
        else if (ex_unmapped && ID_EX_memRead)
            ex_trap_cause = CAUSE_UNMAPPED_LOAD;
        else
            ex_trap_cause = CAUSE_UNMAPPED_STORE;
    end

    wire [31:0] predicted_next = ID_EX_pred_taken ? ID_EX_pred_target : idex_pc_plus4;
    reg [31:0] actual_next;
    always @* begin
        actual_next = idex_pc_plus4;
        if (ID_EX_branch)
            actual_next = branch_taken ? branch_target : idex_pc_plus4;
        if (ID_EX_jump)
            actual_next = jump_target;
        if (ID_EX_eret)
            actual_next = csr_epc;
        if (ex_trap)
            actual_next = TRAP_VEC;
    end

    wire idex_fire = ID_EX_valid && exmem_can_accept && !forward_blocked;
    wire ex_mispredict = ID_EX_valid && !ex_trap && (actual_next != predicted_next);
    wire resolve_redirect = idex_fire && (ex_trap || (actual_next != predicted_next));

    assign csr_trap_set = idex_fire && ex_trap;
    assign csr_trap_epc = ID_EX_pc;
    assign csr_trap_cause = ex_trap_cause;
    assign csr_retire_event = !verification_halt && MEM_WB_valid && !MEM_WB_trap;

    always @* begin
        bpu_update_en = idex_fire && ID_EX_branch && !ex_trap;
        bpu_update_pc = ID_EX_pc;
        bpu_update_taken = branch_taken;
        bpu_update_target = branch_target;
    end

    wire load_hazard;
    hazard_unit u_hazard (
        .ID_EX_valid(ID_EX_valid), .ID_EX_memRead(ID_EX_memRead),
        .ID_EX_rt(ID_EX_rt), .IF_ID_valid(IF_ID_valid),
        .IF_ID_rs(dec_rs), .IF_ID_rt(dec_rt),
        .IF_ID_usesRs(dec_usesRs), .IF_ID_usesRt(dec_usesRt),
        .stall(load_hazard), .pcWrite(), .IF_ID_Write(), .controlBubble()
    );

    wire idex_slot_available = !ID_EX_valid || idex_fire;
    wire ifid_to_idex = IF_ID_valid && idex_slot_available &&
                        !load_hazard && !resolve_redirect;
    wire ifid_slot_available = !IF_ID_valid || ifid_to_idex;
    wire ifbuf_to_ifid = if_buf_valid && ifid_slot_available && !resolve_redirect;
    // A slave may consume the old response and accept the next request on the
    // same edge. If it cannot, the offered turnover request is latched and
    // remains stable as an ordinary presented request.
    assign ibus_resp_ready = if_outstanding &&
                             (if_outstanding_stale || resolve_redirect ||
                              !if_buf_valid || ifbuf_to_ifid);
    wire turnover_offer = if_outstanding && ibus_resp_valid && ibus_resp_ready &&
                          !if_outstanding_stale && !resolve_redirect && fetch_enable;
    assign ibus_req_valid = if_req_present || turnover_offer;
    assign ibus_req_addr = if_req_present ? if_req_addr_reg : fetch_pc;
    wire active_req_stale = if_req_present ? if_req_stale : 1'b0;
    wire active_req_pred_taken = if_req_present ? if_req_pred_taken : launch_pred_taken;
    wire [31:0] active_req_pred_target = if_req_present ? if_req_pred_target
                                                        : launch_pred_target;
    wire [63:0] active_req_uid = if_req_present ? if_req_uid : next_fetch_uid;

    wire fetch_can_launch = !if_req_present && !if_outstanding &&
                            (!if_buf_valid || ifbuf_to_ifid) &&
                            fetch_enable && !resolve_redirect;

    assign redirect_event = resolve_redirect;
    assign pipeline_stall_event = (EX_MEM_valid && !exmem_complete) ||
                                  (ID_EX_valid && forward_blocked) || load_hazard;

    // ---------------------------------------------------------------------
    // Fetch sequential owner
    // ---------------------------------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            fetch_pc                    <= RESET_PC;
            next_fetch_uid              <= 64'd0;
            if_req_present              <= 1'b0;
            if_req_addr_reg             <= RESET_PC;
            if_req_stale                <= 1'b0;
            if_req_pred_taken           <= 1'b0;
            if_req_pred_target          <= 32'b0;
            if_req_uid                  <= 64'b0;
            if_outstanding              <= 1'b0;
            if_outstanding_stale        <= 1'b0;
            if_outstanding_pc           <= 32'b0;
            if_outstanding_pred_taken   <= 1'b0;
            if_outstanding_pred_target  <= 32'b0;
            if_outstanding_uid          <= 64'b0;
            if_buf_valid                <= 1'b0;
            if_buf_instr                <= 32'b0;
            if_buf_pc                   <= 32'b0;
            if_buf_pred_taken           <= 1'b0;
            if_buf_pred_target          <= 32'b0;
            if_buf_uid                  <= 64'b0;
        end else begin
            if (ifbuf_to_ifid)
                if_buf_valid <= 1'b0;

            if (fetch_can_launch) begin
                if_req_present     <= 1'b1;
                if_req_addr_reg    <= fetch_pc;
                if_req_stale       <= 1'b0;
                if_req_pred_taken  <= launch_pred_taken;
                if_req_pred_target <= launch_pred_target;
                if_req_uid         <= next_fetch_uid;
            end

            if (ibus_req_fire) begin
                if_req_present             <= 1'b0;
                if_outstanding             <= 1'b1;
                if_outstanding_stale       <= active_req_stale || resolve_redirect;
                if_outstanding_pc          <= ibus_req_addr;
                if_outstanding_pred_taken  <= active_req_pred_taken;
                if_outstanding_pred_target <= active_req_pred_target;
                if_outstanding_uid         <= active_req_uid;
                if_req_stale               <= 1'b0;
                next_fetch_uid             <= next_fetch_uid + 64'd1;
                if (!active_req_stale && !resolve_redirect)
                    fetch_pc <= active_req_pred_taken ? active_req_pred_target
                                                   : (ibus_req_addr + 32'd4);
            end

            if (ibus_resp_fire) begin
                if_outstanding <= 1'b0;
                if_outstanding_stale <= 1'b0;
                if (!if_outstanding_stale && !resolve_redirect) begin
                    if_buf_valid       <= 1'b1;
                    if_buf_instr       <= ibus_resp_rdata;
                    if_buf_pc          <= if_outstanding_pc;
                    if_buf_pred_taken  <= if_outstanding_pred_taken;
                    if_buf_pred_target <= if_outstanding_pred_target;
                    if_buf_uid         <= if_outstanding_uid;
                end
            end

            // A legal response/request turnover keeps exactly one accepted
            // request outstanding. This assignment has priority over freeing
            // the old response owner above.
            if (ibus_resp_fire && ibus_req_fire) begin
                if_outstanding       <= 1'b1;
                if_outstanding_stale <= active_req_stale || resolve_redirect;
            end

            if (turnover_offer && !ibus_req_fire) begin
                if_req_present     <= 1'b1;
                if_req_addr_reg    <= fetch_pc;
                if_req_stale       <= 1'b0;
                if_req_pred_taken  <= launch_pred_taken;
                if_req_pred_target <= launch_pred_target;
                if_req_uid         <= next_fetch_uid;
            end

            if (resolve_redirect) begin
                fetch_pc <= actual_next;
                if_buf_valid <= 1'b0;
                if (if_req_present && !ibus_req_fire)
                    if_req_stale <= 1'b1;
                if (if_outstanding && !ibus_resp_fire)
                    if_outstanding_stale <= 1'b1;
            end

            if (verification_halt) begin
                if_req_present <= 1'b0;
                if_buf_valid   <= 1'b0;
                if (if_outstanding && !ibus_resp_fire)
                    if_outstanding_stale <= 1'b1;
                if (ibus_resp_fire) begin
                    if_outstanding       <= 1'b0;
                    if_outstanding_stale <= 1'b0;
                end
            end
        end
    end

    // ---------------------------------------------------------------------
    // IF/ID boundary
    // ---------------------------------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            IF_ID_valid       <= 1'b0;
            IF_ID_pc          <= 32'b0;
            IF_ID_instr       <= 32'b0;
            IF_ID_pred_taken  <= 1'b0;
            IF_ID_pred_target <= 32'b0;
            IF_ID_uid         <= 64'b0;
        end else if (verification_halt) begin
            IF_ID_valid <= 1'b0;
        end else if (resolve_redirect) begin
            IF_ID_valid <= 1'b0;
        end else if (ifid_slot_available) begin
            IF_ID_valid <= ifbuf_to_ifid;
            if (ifbuf_to_ifid) begin
                IF_ID_pc          <= if_buf_pc;
                IF_ID_instr       <= if_buf_instr;
                IF_ID_pred_taken  <= if_buf_pred_taken;
                IF_ID_pred_target <= if_buf_pred_target;
                IF_ID_uid         <= if_buf_uid;
            end
        end
    end

    // ---------------------------------------------------------------------
    // ID/EX boundary
    // ---------------------------------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ID_EX_valid       <= 1'b0;
            ID_EX_pc          <= 32'b0;
            ID_EX_instr       <= 32'b0;
            ID_EX_pred_taken  <= 1'b0;
            ID_EX_pred_target <= 32'b0;
            ID_EX_uid         <= 64'b0;
            ID_EX_regData1    <= 32'b0;
            ID_EX_regData2    <= 32'b0;
            ID_EX_immExt      <= 32'b0;
            ID_EX_rs          <= 5'b0;
            ID_EX_rt          <= 5'b0;
            ID_EX_rd          <= 5'b0;
            ID_EX_shamt       <= 5'b0;
            ID_EX_funct       <= 6'b0;
            ID_EX_opcode      <= 6'b0;
            ID_EX_usesRs      <= 1'b0;
            ID_EX_usesRt      <= 1'b0;
            ID_EX_illegal     <= 1'b0;
            ID_EX_regWrite    <= 1'b0;
            ID_EX_memRead     <= 1'b0;
            ID_EX_memWrite    <= 1'b0;
            ID_EX_memToReg    <= 1'b0;
            ID_EX_branch      <= 1'b0;
            ID_EX_branch_ne   <= 1'b0;
            ID_EX_jump        <= 1'b0;
            ID_EX_eret        <= 1'b0;
            ID_EX_aluOp       <= 3'b0;
            ID_EX_aluSrc      <= 1'b0;
            ID_EX_regDst      <= 1'b0;
            ID_EX_jal         <= 1'b0;
            ID_EX_memSize     <= MSIZE_W;
            ID_EX_loadSigned  <= 1'b1;
        end else if (verification_halt) begin
            ID_EX_valid <= 1'b0;
        end else if (resolve_redirect) begin
            ID_EX_valid <= 1'b0;
        end else if (idex_slot_available) begin
            ID_EX_valid <= ifid_to_idex;
            if (ifid_to_idex) begin
                ID_EX_pc          <= IF_ID_pc;
                ID_EX_instr       <= IF_ID_instr;
                ID_EX_pred_taken  <= IF_ID_pred_taken;
                ID_EX_pred_target <= IF_ID_pred_target;
                ID_EX_uid         <= IF_ID_uid;
                ID_EX_regData1    <= dec_regData1;
                ID_EX_regData2    <= dec_regData2;
                ID_EX_immExt      <= dec_imm_ext;
                ID_EX_rs          <= dec_rs;
                ID_EX_rt          <= dec_rt;
                ID_EX_rd          <= dec_rd;
                ID_EX_shamt       <= dec_shamt;
                ID_EX_funct       <= dec_funct;
                ID_EX_opcode      <= dec_opcode;
                ID_EX_usesRs      <= dec_usesRs;
                ID_EX_usesRt      <= dec_usesRt;
                ID_EX_illegal     <= dec_illegal;
                ID_EX_regWrite    <= dec_regWrite;
                ID_EX_memRead     <= dec_memRead;
                ID_EX_memWrite    <= dec_memWrite;
                ID_EX_memToReg    <= dec_memToReg;
                ID_EX_branch      <= dec_branch;
                ID_EX_branch_ne   <= dec_branch_ne;
                ID_EX_jump        <= dec_jump;
                ID_EX_eret        <= dec_eret;
                ID_EX_aluOp       <= dec_aluOp;
                ID_EX_aluSrc      <= dec_aluSrc;
                ID_EX_regDst      <= dec_regDst;
                ID_EX_jal         <= dec_jal;
                ID_EX_memSize     <= dec_memSize;
                ID_EX_loadSigned  <= dec_loadSigned;
            end
        end else if (ID_EX_valid) begin
            // A downstream memory wait may hold this instruction after an
            // older producer has reached WB.  Preserve any value forwarded
            // during the hold so it cannot disappear when that producer
            // leaves the pipeline before this instruction finally fires.
            if (forwardA == 2'b10)
                ID_EX_regData1 <= exmem_result_value;
            else if (forwardA == 2'b01)
                ID_EX_regData1 <= MEM_WB_result;
            if (forwardB == 2'b10)
                ID_EX_regData2 <= exmem_result_value;
            else if (forwardB == 2'b01)
                ID_EX_regData2 <= MEM_WB_result;
        end
    end

    // ---------------------------------------------------------------------
    // EX/MEM boundary and transaction state
    // ---------------------------------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            EX_MEM_valid        <= 1'b0;
            EX_MEM_pc           <= 32'b0;
            EX_MEM_instr        <= 32'b0;
            EX_MEM_next_pc      <= 32'b0;
            EX_MEM_uid          <= 64'b0;
            EX_MEM_trap         <= 1'b0;
            EX_MEM_cause        <= 32'b0;
            EX_MEM_epc          <= 32'b0;
            EX_MEM_aluResult    <= 32'b0;
            EX_MEM_storeData    <= 32'b0;
            EX_MEM_storeAligned <= 32'b0;
            EX_MEM_storeStrobe  <= 4'b0;
            EX_MEM_pc_link      <= 32'b0;
            EX_MEM_regDest      <= 5'b0;
            EX_MEM_regWrite     <= 1'b0;
            EX_MEM_memRead      <= 1'b0;
            EX_MEM_memWrite     <= 1'b0;
            EX_MEM_memToReg     <= 1'b0;
            EX_MEM_jal          <= 1'b0;
            EX_MEM_memSize      <= MSIZE_W;
            EX_MEM_loadSigned   <= 1'b1;
        end else if (verification_halt) begin
            EX_MEM_valid <= 1'b0;
        end else if (exmem_can_accept) begin
            EX_MEM_valid <= idex_fire;
            if (idex_fire) begin
                EX_MEM_pc           <= ID_EX_pc;
                EX_MEM_instr        <= ID_EX_instr;
                EX_MEM_next_pc      <= actual_next;
                EX_MEM_uid          <= ID_EX_uid;
                EX_MEM_trap         <= ex_trap;
                EX_MEM_cause        <= ex_trap ? ex_trap_cause : 32'b0;
                EX_MEM_epc          <= ex_trap ? ID_EX_pc : 32'b0;
                EX_MEM_aluResult    <= alu_result;
                EX_MEM_storeData    <= fwdB;
                EX_MEM_storeAligned <= ex_store_aligned;
                EX_MEM_storeStrobe  <= ex_store_strobe;
                EX_MEM_pc_link      <= idex_pc_plus4;
                EX_MEM_regDest      <= idex_reg_dest;
                EX_MEM_regWrite     <= ID_EX_regWrite && !ex_trap;
                EX_MEM_memRead      <= ID_EX_memRead && !ex_trap;
                EX_MEM_memWrite     <= ID_EX_memWrite && !ex_trap;
                EX_MEM_memToReg     <= ID_EX_memToReg && !ex_trap;
                EX_MEM_jal          <= ID_EX_jal && !ex_trap;
                EX_MEM_memSize      <= ID_EX_memSize;
                EX_MEM_loadSigned   <= ID_EX_loadSigned;
            end
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            EX_MEM_mem_accepted <= 1'b0;
        else if (verification_halt)
            EX_MEM_mem_accepted <= 1'b0;
        else if (exmem_can_accept)
            EX_MEM_mem_accepted <= 1'b0;
        else if (dbus_req_fire)
            EX_MEM_mem_accepted <= 1'b1;
    end

    // ---------------------------------------------------------------------
    // MEM/WB boundary
    // ---------------------------------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            MEM_WB_valid     <= 1'b0;
            MEM_WB_pc        <= 32'b0;
            MEM_WB_instr     <= 32'b0;
            MEM_WB_next_pc   <= 32'b0;
            MEM_WB_uid       <= 64'b0;
            MEM_WB_trap      <= 1'b0;
            MEM_WB_cause     <= 32'b0;
            MEM_WB_epc       <= 32'b0;
            MEM_WB_result    <= 32'b0;
            MEM_WB_regDest   <= 5'b0;
            MEM_WB_regWrite  <= 1'b0;
            MEM_WB_memValid  <= 1'b0;
            MEM_WB_memWrite  <= 1'b0;
            MEM_WB_memAddr   <= 32'b0;
            MEM_WB_memSize   <= MSIZE_W;
            MEM_WB_storeData <= 32'b0;
            MEM_WB_memWdata  <= 32'b0;
            MEM_WB_memWstrb  <= 4'b0;
            MEM_WB_memRaw    <= 32'b0;
            MEM_WB_memRdata  <= 32'b0;
        end else if (verification_halt) begin
            MEM_WB_valid <= 1'b0;
        end else begin
            MEM_WB_valid <= exmem_fire;
            if (exmem_fire) begin
                MEM_WB_pc        <= EX_MEM_pc;
                MEM_WB_instr     <= EX_MEM_instr;
                MEM_WB_next_pc   <= EX_MEM_next_pc;
                MEM_WB_uid       <= EX_MEM_uid;
                MEM_WB_trap      <= EX_MEM_trap;
                MEM_WB_cause     <= EX_MEM_cause;
                MEM_WB_epc       <= EX_MEM_epc;
                MEM_WB_result    <= exmem_result_value;
                MEM_WB_regDest   <= EX_MEM_regDest;
                MEM_WB_regWrite  <= EX_MEM_regWrite;
                MEM_WB_memValid  <= !EX_MEM_trap && (EX_MEM_memRead || EX_MEM_memWrite);
                MEM_WB_memWrite  <= !EX_MEM_trap && EX_MEM_memWrite;
                MEM_WB_memAddr   <= EX_MEM_aluResult;
                MEM_WB_memSize   <= EX_MEM_memSize;
                MEM_WB_storeData <= EX_MEM_storeData;
                MEM_WB_memWdata  <= EX_MEM_storeAligned;
                MEM_WB_memWstrb  <= EX_MEM_memWrite ? EX_MEM_storeStrobe : 4'b0;
                MEM_WB_memRaw    <= (EX_MEM_memRead && !exmem_csr_read) ? dbus_resp_rdata :
                                    (exmem_csr_read ? csr_rdata : 32'b0);
                MEM_WB_memRdata  <= EX_MEM_memRead ? exmem_result_value : 32'b0;
            end
        end
    end

    // ---------------------------------------------------------------------
    // Registered retirement observation
    // ---------------------------------------------------------------------
    reg [63:0] cycle_count;
    reg [63:0] event_order_count;
    reg [63:0] retire_order_count;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cycle_count        <= 64'b0;
            event_order_count  <= 64'b0;
            retire_order_count <= 64'b0;
            trace_valid        <= 1'b0;
            trace_epoch        <= 32'b0;
            trace_event_order  <= 64'b0;
            trace_retire_order <= 64'b0;
            trace_cycle        <= 64'b0;
            trace_uid          <= 64'b0;
            trace_trap         <= 1'b0;
            trace_pc           <= 32'b0;
            trace_instr        <= 32'b0;
            trace_next_pc      <= 32'b0;
            trace_rd_we        <= 1'b0;
            trace_rd           <= 5'b0;
            trace_rd_data      <= 32'b0;
            trace_mem_valid    <= 1'b0;
            trace_mem_write    <= 1'b0;
            trace_mem_addr     <= 32'b0;
            trace_mem_size     <= MSIZE_W;
            trace_store_data   <= 32'b0;
            trace_mem_wdata    <= 32'b0;
            trace_mem_wstrb    <= 4'b0;
            trace_mem_raw      <= 32'b0;
            trace_mem_rdata    <= 32'b0;
            trace_cause        <= 32'b0;
            trace_epc          <= 32'b0;
        end else if (verification_halt) begin
            cycle_count <= cycle_count + 64'd1;
            trace_valid <= 1'b0;
        end else begin
            cycle_count <= cycle_count + 64'd1;
            trace_valid <= MEM_WB_valid;
            if (MEM_WB_valid) begin
                trace_epoch        <= 32'b0;
                trace_event_order  <= event_order_count;
                trace_retire_order <= retire_order_count;
                trace_cycle        <= cycle_count;
                trace_uid          <= MEM_WB_uid;
                trace_trap         <= MEM_WB_trap;
                trace_pc           <= MEM_WB_pc;
                trace_instr        <= MEM_WB_instr;
                trace_next_pc      <= MEM_WB_next_pc;
                trace_rd_we        <= !MEM_WB_trap && MEM_WB_regWrite &&
                                      (MEM_WB_regDest != 5'd0);
                trace_rd           <= (!MEM_WB_trap && MEM_WB_regWrite &&
                                       (MEM_WB_regDest != 5'd0)) ? MEM_WB_regDest : 5'd0;
                trace_rd_data      <= (!MEM_WB_trap && MEM_WB_regWrite &&
                                       (MEM_WB_regDest != 5'd0)) ? MEM_WB_result : 32'b0;
                trace_mem_valid    <= MEM_WB_memValid;
                trace_mem_write    <= MEM_WB_memValid && MEM_WB_memWrite;
                trace_mem_addr     <= MEM_WB_memValid ? MEM_WB_memAddr : 32'b0;
                trace_mem_size     <= MEM_WB_memValid ? MEM_WB_memSize : MSIZE_W;
                trace_store_data   <= (MEM_WB_memValid && MEM_WB_memWrite) ?
                                      MEM_WB_storeData : 32'b0;
                trace_mem_wdata    <= (MEM_WB_memValid && MEM_WB_memWrite) ?
                                      MEM_WB_memWdata : 32'b0;
                trace_mem_wstrb    <= (MEM_WB_memValid && MEM_WB_memWrite) ?
                                      MEM_WB_memWstrb : 4'b0;
                trace_mem_raw      <= (MEM_WB_memValid && !MEM_WB_memWrite) ?
                                      MEM_WB_memRaw : 32'b0;
                trace_mem_rdata    <= (MEM_WB_memValid && !MEM_WB_memWrite) ?
                                      MEM_WB_memRdata : 32'b0;
                trace_cause        <= MEM_WB_cause;
                trace_epc          <= MEM_WB_epc;
                event_order_count  <= event_order_count + 64'd1;
                if (!MEM_WB_trap)
                    retire_order_count <= retire_order_count + 64'd1;
            end
        end
    end

    assign core_idle = !if_req_present && !if_outstanding && !if_buf_valid &&
                       !IF_ID_valid && !ID_EX_valid && !EX_MEM_valid &&
                       !MEM_WB_valid;

    assign dbg_forward_a       = forwardA;
    assign dbg_forward_b       = forwardB;
    assign dbg_forward_blocked = forward_blocked;
    assign dbg_load_hazard     = load_hazard && idex_fire;
    assign dbg_wb_id_bypass_rs = wb_id_bypass_rs && ifid_to_idex;
    assign dbg_wb_id_bypass_rt = wb_id_bypass_rt && ifid_to_idex;
    assign dbg_redirect        = resolve_redirect;
    assign dbg_mispredict      = idex_fire && ex_mispredict;
    assign dbg_branch_resolve  = idex_fire && ID_EX_branch && !ex_trap;
    assign dbg_fetch_discard   = ibus_resp_fire && (if_outstanding_stale || resolve_redirect);
    assign dbg_exmem_wait      = EX_MEM_valid && !exmem_complete;

endmodule
