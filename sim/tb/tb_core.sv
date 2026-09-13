`timescale 1ns/1ps
`default_nettype none

module tb_core #(
    parameter integer ENABLE_BPU     = 1,
    parameter integer I_RANDOM_WAIT  = 0,
    parameter integer I_WAIT_MAX     = 0,
    parameter integer I_FIXED_WAIT   = 0,
    parameter [31:0]  I_WAIT_SEED    = 32'h1357_2468,
    parameter integer D_RANDOM_WAIT  = 0,
    parameter integer D_WAIT_MAX     = 0,
    parameter integer D_FIXED_WAIT   = 0,
    parameter [31:0]  D_WAIT_SEED    = 32'h89ab_cdef,
    parameter integer BUS_ADVERSARIAL = 0,
    parameter integer I_REQ_STALL_CYCLES = 4,
    parameter integer D_REQ_STALL_CYCLES = 4,
    parameter integer I_RESP_HOLD_CYCLES = 2,
    parameter integer D_RESP_HOLD_CYCLES = 12
);
    localparam integer MEM_BYTES = 4096;
    localparam integer MEM_WORDS = MEM_BYTES / 4;

    reg clk = 1'b0;
    reg rst_n = 1'b1;
    reg fetch_enable = 1'b0;
    reg verification_halt = 1'b0;
    reg [31:0] completion_pc;
    always #5 clk = ~clk;

    wire ib_req_v;
    wire ib_req_r;
    wire [31:0] ib_req_a;
    wire ib_resp_v;
    wire ib_resp_r;
    wire [31:0] ib_resp_d;

    function automatic instruction_may_redirect;
        input [31:0] instruction;
        reg [5:0] opcode;
        reg [5:0] funct;
        begin
            opcode = instruction[31:26];
            funct = instruction[5:0];
            case (opcode)
                6'h00: instruction_may_redirect =
                    !((funct == 6'h00) || (funct == 6'h02) || (funct == 6'h03) ||
                      (funct == 6'h20) || (funct == 6'h21) || (funct == 6'h22) ||
                      (funct == 6'h23) || (funct == 6'h24) || (funct == 6'h25) ||
                      (funct == 6'h26) || (funct == 6'h27) || (funct == 6'h2a));
                6'h08, 6'h0c, 6'h0d, 6'h0e, 6'h0f:
                    instruction_may_redirect = 1'b0;
                default:
                    // Branches, jumps, ERET/illegal words, and memory
                    // instructions can still redirect or trap in EX.
                    instruction_may_redirect = 1'b1;
            endcase
        end
    endfunction

    // Do not stop on a completion fetch that is still speculative behind an
    // unresolved older instruction.  ID/EX resolves on this edge and is
    // covered by resolve_redirect; IF/ID and the fetch buffer resolve later.
    wire older_unresolved_redirect =
        (dut.IF_ID_valid && instruction_may_redirect(dut.IF_ID_instr)) ||
        (dut.if_buf_valid && instruction_may_redirect(dut.if_buf_instr));
    wire completion_response_now = ib_resp_v && ib_resp_r &&
                                   (dut.if_outstanding_pc == completion_pc) &&
                                   !dut.if_outstanding_stale &&
                                   !dut.resolve_redirect &&
                                   !older_unresolved_redirect;
    wire core_fetch_enable = fetch_enable && !completion_response_now;

    wire db_req_v;
    wire db_req_r;
    wire [31:0] db_req_a;
    wire [31:0] db_req_wd;
    wire [3:0] db_req_ws;
    wire db_resp_v;
    wire db_resp_r;
    wire [31:0] db_resp_d;

    // Core-facing signals above are still used by every existing checker.
    // These separate signals expose the actual memory-facing hold cycles.
    wire im_req_v, im_req_r, im_resp_v, im_resp_r;
    wire [31:0] im_req_a, im_resp_d;
    wire dm_req_v, dm_req_r, dm_resp_v, dm_resp_r;
    wire [31:0] dm_req_a, dm_req_wd, dm_resp_d;
    wire [3:0] dm_req_ws;
    wire ibus_shim_idle, dbus_shim_idle;

    generate
        if (BUS_ADVERSARIAL != 0) begin : adversarial_bus
            rv_bus_shim #(
                .REQUEST_STALL_CYCLES(I_REQ_STALL_CYCLES),
                .RESPONSE_HOLD_CYCLES(I_RESP_HOLD_CYCLES)
            ) ibus_shim (
                .clk(clk), .rst_n(rst_n),
                .s_req_valid(ib_req_v), .s_req_ready(ib_req_r),
                .s_req_addr(ib_req_a), .s_req_wdata(32'b0), .s_req_wstrb(4'b0),
                .s_resp_valid(ib_resp_v), .s_resp_ready(ib_resp_r), .s_resp_rdata(ib_resp_d),
                .m_req_valid(im_req_v), .m_req_ready(im_req_r), .m_req_addr(im_req_a),
                .m_req_wdata(), .m_req_wstrb(),
                .m_resp_valid(im_resp_v), .m_resp_ready(im_resp_r), .m_resp_rdata(im_resp_d),
                .idle(ibus_shim_idle)
            );
            rv_bus_shim #(
                .REQUEST_STALL_CYCLES(D_REQ_STALL_CYCLES),
                .RESPONSE_HOLD_CYCLES(D_RESP_HOLD_CYCLES)
            ) dbus_shim (
                .clk(clk), .rst_n(rst_n),
                .s_req_valid(db_req_v), .s_req_ready(db_req_r),
                .s_req_addr(db_req_a), .s_req_wdata(db_req_wd), .s_req_wstrb(db_req_ws),
                .s_resp_valid(db_resp_v), .s_resp_ready(db_resp_r), .s_resp_rdata(db_resp_d),
                .m_req_valid(dm_req_v), .m_req_ready(dm_req_r),
                .m_req_addr(dm_req_a), .m_req_wdata(dm_req_wd), .m_req_wstrb(dm_req_ws),
                .m_resp_valid(dm_resp_v), .m_resp_ready(dm_resp_r), .m_resp_rdata(dm_resp_d),
                .idle(dbus_shim_idle)
            );
        end else begin : direct_bus
            assign im_req_v = ib_req_v;
            assign ib_req_r = im_req_r;
            assign im_req_a = ib_req_a;
            assign ib_resp_v = im_resp_v;
            assign im_resp_r = ib_resp_r;
            assign ib_resp_d = im_resp_d;
            assign dm_req_v = db_req_v;
            assign db_req_r = dm_req_r;
            assign dm_req_a = db_req_a;
            assign dm_req_wd = db_req_wd;
            assign dm_req_ws = db_req_ws;
            assign db_resp_v = dm_resp_v;
            assign dm_resp_r = db_resp_r;
            assign db_resp_d = dm_resp_d;
            assign ibus_shim_idle = !im_req_v && !im_resp_v;
            assign dbus_shim_idle = !dm_req_v && !dm_resp_v;
        end
    endgenerate

    wire trace_valid;
    wire [31:0] trace_epoch;
    wire [63:0] trace_event_order;
    wire [63:0] trace_retire_order;
    wire [63:0] trace_cycle;
    wire [63:0] trace_uid;
    wire trace_trap;
    wire [31:0] trace_pc;
    wire [31:0] trace_instr;
    wire [31:0] trace_next_pc;
    wire trace_rd_we;
    wire [4:0] trace_rd;
    wire [31:0] trace_rd_data;
    wire trace_mem_valid;
    wire trace_mem_write;
    wire [31:0] trace_mem_addr;
    wire [1:0] trace_mem_size;
    wire [31:0] trace_store_data;
    wire [31:0] trace_mem_wdata;
    wire [3:0] trace_mem_wstrb;
    wire [31:0] trace_mem_raw;
    wire [31:0] trace_mem_rdata;
    wire [31:0] trace_cause;
    wire [31:0] trace_epc;
    wire core_idle;

    wire [1:0] dbg_forward_a;
    wire [1:0] dbg_forward_b;
    wire dbg_forward_blocked;
    wire dbg_load_hazard;
    wire dbg_wb_id_bypass_rs;
    wire dbg_wb_id_bypass_rt;
    wire dbg_redirect;
    wire dbg_mispredict;
    wire dbg_branch_resolve;
    wire dbg_fetch_discard;
    wire dbg_exmem_wait;

    cpu_core #(
        .DMEM_BYTES(MEM_BYTES),
        .ENABLE_BPU(ENABLE_BPU)
    ) dut (
        .clk(clk), .rst_n(rst_n),
        .ibus_req_valid(ib_req_v), .ibus_req_ready(ib_req_r),
        .ibus_req_addr(ib_req_a), .ibus_resp_valid(ib_resp_v),
        .ibus_resp_ready(ib_resp_r), .ibus_resp_rdata(ib_resp_d),
        .dbus_req_valid(db_req_v), .dbus_req_ready(db_req_r),
        .dbus_req_addr(db_req_a), .dbus_req_wdata(db_req_wd),
        .dbus_req_wstrb(db_req_ws), .dbus_resp_valid(db_resp_v),
        .dbus_resp_ready(db_resp_r), .dbus_resp_rdata(db_resp_d),
        .fetch_enable(core_fetch_enable),
        .verification_halt(verification_halt),
        .trace_valid(trace_valid), .trace_epoch(trace_epoch),
        .trace_event_order(trace_event_order),
        .trace_retire_order(trace_retire_order), .trace_cycle(trace_cycle),
        .trace_uid(trace_uid), .trace_trap(trace_trap), .trace_pc(trace_pc),
        .trace_instr(trace_instr), .trace_next_pc(trace_next_pc),
        .trace_rd_we(trace_rd_we), .trace_rd(trace_rd),
        .trace_rd_data(trace_rd_data), .trace_mem_valid(trace_mem_valid),
        .trace_mem_write(trace_mem_write), .trace_mem_addr(trace_mem_addr),
        .trace_mem_size(trace_mem_size), .trace_store_data(trace_store_data),
        .trace_mem_wdata(trace_mem_wdata), .trace_mem_wstrb(trace_mem_wstrb),
        .trace_mem_raw(trace_mem_raw), .trace_mem_rdata(trace_mem_rdata),
        .trace_cause(trace_cause), .trace_epc(trace_epc),
        .core_idle(core_idle),
        .dbg_forward_a(dbg_forward_a), .dbg_forward_b(dbg_forward_b),
        .dbg_forward_blocked(dbg_forward_blocked),
        .dbg_load_hazard(dbg_load_hazard),
        .dbg_wb_id_bypass_rs(dbg_wb_id_bypass_rs),
        .dbg_wb_id_bypass_rt(dbg_wb_id_bypass_rt),
        .dbg_redirect(dbg_redirect), .dbg_mispredict(dbg_mispredict),
        .dbg_branch_resolve(dbg_branch_resolve),
        .dbg_fetch_discard(dbg_fetch_discard),
        .dbg_exmem_wait(dbg_exmem_wait)
    );

    rv_rom #(
        .BYTES(MEM_BYTES), .RANDOM_WAIT(I_RANDOM_WAIT),
        .WAIT_MAX(I_WAIT_MAX), .FIXED_WAIT(I_FIXED_WAIT),
        .WAIT_SEED(I_WAIT_SEED)
    ) irom (
        .clk(clk), .rst_n(rst_n),
        .req_valid(im_req_v), .req_ready(im_req_r), .req_addr(im_req_a),
        .resp_valid(im_resp_v), .resp_ready(im_resp_r),
        .resp_rdata(im_resp_d)
    );

    rv_mem #(
        .BYTES(MEM_BYTES), .RANDOM_WAIT(D_RANDOM_WAIT),
        .WAIT_MAX(D_WAIT_MAX), .FIXED_WAIT(D_FIXED_WAIT),
        .WAIT_SEED(D_WAIT_SEED)
    ) dram (
        .clk(clk), .rst_n(rst_n),
        .req_valid(dm_req_v), .req_ready(dm_req_r),
        .req_addr(dm_req_a), .req_wdata(dm_req_wd), .req_wstrb(dm_req_ws),
        .resp_valid(dm_resp_v), .resp_ready(dm_resp_r),
        .resp_rdata(dm_resp_d)
    );

    wire scoreboard_idle;
    wire [63:0] scoreboard_request_count;
    wire [63:0] scoreboard_response_count;
    wire [63:0] scoreboard_store_count;
    wire [31:0] scoreboard_error_count;
    wire [31:0] monitor_error_count;

    scoreboard u_scoreboard (
        .clk(clk), .rst_n(rst_n),
        .dbus_req_valid(db_req_v), .dbus_req_ready(db_req_r),
        .dbus_req_addr(db_req_a), .dbus_req_wdata(db_req_wd),
        .dbus_req_wstrb(db_req_ws),
        .dbus_resp_valid(db_resp_v), .dbus_resp_ready(db_resp_r),
        .dbus_resp_rdata(db_resp_d),
        .trace_valid(trace_valid), .trace_uid(trace_uid),
        .trace_trap(trace_trap), .trace_mem_valid(trace_mem_valid),
        .trace_mem_write(trace_mem_write), .trace_mem_addr(trace_mem_addr),
        .trace_mem_size(trace_mem_size), .trace_mem_wdata(trace_mem_wdata),
        .trace_mem_wstrb(trace_mem_wstrb), .trace_mem_rdata(trace_mem_rdata),
        .idle(scoreboard_idle), .request_count(scoreboard_request_count),
        .response_count(scoreboard_response_count),
        .store_count(scoreboard_store_count),
        .error_count(scoreboard_error_count)
    );

    core_monitors u_monitors (
        .clk(clk), .rst_n(rst_n), .r0_value(dut.u_rf.rf[0]),
        .core_idle(core_idle),
        .ibus_req_valid(ib_req_v), .ibus_req_ready(ib_req_r),
        .ibus_req_addr(ib_req_a), .ibus_resp_valid(ib_resp_v),
        .ibus_resp_ready(ib_resp_r), .ibus_resp_rdata(ib_resp_d),
        .dbus_req_valid(db_req_v), .dbus_req_ready(db_req_r),
        .dbus_req_addr(db_req_a), .dbus_req_wdata(db_req_wd),
        .dbus_req_wstrb(db_req_ws), .dbus_resp_valid(db_resp_v),
        .dbus_resp_ready(db_resp_r), .dbus_resp_rdata(db_resp_d),
        .trace_valid(trace_valid), .trace_event_order(trace_event_order),
        .trace_retire_order(trace_retire_order), .trace_uid(trace_uid),
        .trace_trap(trace_trap), .trace_rd_we(trace_rd_we),
        .trace_rd(trace_rd), .trace_mem_valid(trace_mem_valid),
        .trace_mem_write(trace_mem_write), .trace_mem_wstrb(trace_mem_wstrb),
        .error_count(monitor_error_count)
    );

    string program_file;
    string data_file;
    string trace_file;
    string state_file;
    string coverage_file;
    string wave_file;
    integer trace_fd;
    integer state_fd;
    integer coverage_fd;
    integer max_cycles;
    integer expected_events;
    integer cycle_count;
    integer trace_count;
    integer memory_index;
    reg completion_seen;
    reg finished;

    integer cov_fwd_ex_a;
    integer cov_fwd_ex_b;
    integer cov_fwd_wb_a;
    integer cov_fwd_wb_b;
    integer cov_fwd_double_a;
    integer cov_fwd_double_b;
    integer cov_fwd_blocked;
    integer cov_load_hazard;
    integer cov_wb_id_rs;
    integer cov_wb_id_rt;
    integer cov_ibus_backpressure;
    integer cov_dbus_backpressure;
    integer cov_ibus_resp_hold;
    integer cov_dbus_resp_hold;
    integer cov_ibus_mem_resp_hold;
    integer cov_dbus_mem_resp_hold;
    integer cov_redirect;
    integer cov_redirect_ibus_blocked;
    integer cov_mispredict;
    integer cov_branch_taken;
    integer cov_branch_not_taken;
    integer cov_fetch_discard;
    integer cov_exmem_wait;
    integer cov_store_retire;
    integer cov_load_retire;
    integer cov_trap;
    integer cov_write_r0;

    function integer memory_size_bytes;
        input [1:0] encoded_size;
        begin
            case (encoded_size)
                2'b10: memory_size_bytes = 1;
                2'b01: memory_size_bytes = 2;
                default: memory_size_bytes = 4;
            endcase
        end
    endfunction

    task write_coverage;
        begin
            $fwrite(coverage_fd, "fwd_ex_a=%0d\n", cov_fwd_ex_a);
            $fwrite(coverage_fd, "fwd_ex_b=%0d\n", cov_fwd_ex_b);
            $fwrite(coverage_fd, "fwd_wb_a=%0d\n", cov_fwd_wb_a);
            $fwrite(coverage_fd, "fwd_wb_b=%0d\n", cov_fwd_wb_b);
            $fwrite(coverage_fd, "fwd_double_a=%0d\n", cov_fwd_double_a);
            $fwrite(coverage_fd, "fwd_double_b=%0d\n", cov_fwd_double_b);
            $fwrite(coverage_fd, "fwd_blocked=%0d\n", cov_fwd_blocked);
            $fwrite(coverage_fd, "load_hazard=%0d\n", cov_load_hazard);
            $fwrite(coverage_fd, "wb_id_rs=%0d\n", cov_wb_id_rs);
            $fwrite(coverage_fd, "wb_id_rt=%0d\n", cov_wb_id_rt);
            $fwrite(coverage_fd, "ibus_backpressure=%0d\n", cov_ibus_backpressure);
            $fwrite(coverage_fd, "dbus_backpressure=%0d\n", cov_dbus_backpressure);
            $fwrite(coverage_fd, "ibus_resp_hold=%0d\n", cov_ibus_resp_hold);
            $fwrite(coverage_fd, "dbus_resp_hold=%0d\n", cov_dbus_resp_hold);
            $fwrite(coverage_fd, "ibus_mem_resp_hold=%0d\n", cov_ibus_mem_resp_hold);
            $fwrite(coverage_fd, "dbus_mem_resp_hold=%0d\n", cov_dbus_mem_resp_hold);
            $fwrite(coverage_fd, "redirect=%0d\n", cov_redirect);
            $fwrite(coverage_fd, "redirect_ibus_blocked=%0d\n", cov_redirect_ibus_blocked);
            $fwrite(coverage_fd, "mispredict=%0d\n", cov_mispredict);
            $fwrite(coverage_fd, "branch_taken=%0d\n", cov_branch_taken);
            $fwrite(coverage_fd, "branch_not_taken=%0d\n", cov_branch_not_taken);
            $fwrite(coverage_fd, "fetch_discard=%0d\n", cov_fetch_discard);
            $fwrite(coverage_fd, "exmem_wait=%0d\n", cov_exmem_wait);
            $fwrite(coverage_fd, "store_retire=%0d\n", cov_store_retire);
            $fwrite(coverage_fd, "load_retire=%0d\n", cov_load_retire);
            $fwrite(coverage_fd, "trap=%0d\n", cov_trap);
            $fwrite(coverage_fd, "write_r0=%0d\n", cov_write_r0);
        end
    endtask

    task write_final_state;
        integer register_index;
        begin
            for (register_index = 0; register_index < 32; register_index = register_index + 1)
                $fwrite(state_fd, "REG,%0d,%08x\n", register_index, dut.u_rf.rf[register_index]);
            for (memory_index = 0; memory_index < MEM_WORDS; memory_index = memory_index + 1)
                $fwrite(state_fd, "MEM,%08x,%08x\n", memory_index * 4, dram.mem[memory_index]);
            $fwrite(state_fd, "END,%0d,%0d,%0d\n", trace_count, completion_seen, cycle_count);
            write_coverage();
            $fflush(trace_fd);
            $fflush(state_fd);
            $fflush(coverage_fd);
        end
    endtask

    initial begin
        cycle_count = 0;
        trace_count = 0;
        completion_seen = 1'b0;
        finished = 1'b0;
        verification_halt = 1'b0;
        cov_fwd_ex_a = 0; cov_fwd_ex_b = 0;
        cov_fwd_wb_a = 0; cov_fwd_wb_b = 0;
        cov_fwd_double_a = 0; cov_fwd_double_b = 0;
        cov_fwd_blocked = 0; cov_load_hazard = 0;
        cov_wb_id_rs = 0; cov_wb_id_rt = 0;
        cov_ibus_backpressure = 0; cov_dbus_backpressure = 0;
        cov_ibus_resp_hold = 0; cov_dbus_resp_hold = 0;
        cov_ibus_mem_resp_hold = 0; cov_dbus_mem_resp_hold = 0;
        cov_redirect = 0; cov_mispredict = 0;
        cov_redirect_ibus_blocked = 0;
        cov_branch_taken = 0; cov_branch_not_taken = 0;
        cov_fetch_discard = 0; cov_exmem_wait = 0;
        cov_store_retire = 0; cov_load_retire = 0;
        cov_trap = 0; cov_write_r0 = 0;

        if (!$value$plusargs("PROGRAM=%s", program_file)) begin
            $display("TB_CONFIG_FAIL: missing +PROGRAM=<hex file>");
            $fatal(1);
        end
        if (!$value$plusargs("TRACE=%s", trace_file)) begin
            $display("TB_CONFIG_FAIL: missing +TRACE=<csv file>");
            $fatal(1);
        end
        if (!$value$plusargs("STATE=%s", state_file)) begin
            $display("TB_CONFIG_FAIL: missing +STATE=<state file>");
            $fatal(1);
        end
        if (!$value$plusargs("COVERAGE=%s", coverage_file)) begin
            $display("TB_CONFIG_FAIL: missing +COVERAGE=<coverage file>");
            $fatal(1);
        end
        if (!$value$plusargs("COMPLETION_PC=%h", completion_pc)) begin
            $display("TB_CONFIG_FAIL: missing +COMPLETION_PC=<hex>");
            $fatal(1);
        end
        if (!$value$plusargs("EXPECTED_EVENTS=%d", expected_events)) begin
            $display("TB_CONFIG_FAIL: missing +EXPECTED_EVENTS=<count>");
            $fatal(1);
        end
        if (!$value$plusargs("MAX_CYCLES=%d", max_cycles))
            max_cycles = 2000;

        trace_fd = $fopen(trace_file, "w");
        state_fd = $fopen(state_file, "w");
        coverage_fd = $fopen(coverage_file, "w");
        if ((trace_fd == 0) || (state_fd == 0) || (coverage_fd == 0)) begin
            $display("TB_CONFIG_FAIL: cannot open an output artifact");
            $fatal(1);
        end
        $fwrite(trace_fd,
            "epoch,event_order,retire_order,cycle,uid,trap,pc,instr,next_pc,rd_we,rd,rd_data,mem_valid,mem_write,mem_addr,mem_size,store_data,bus_wdata,wstrb,load_raw,load_value,cause,epc\n");

        #1;
        // Create a real falling edge so every asynchronous-reset block is
        // initialized deterministically; release remains on a negedge.
        rst_n = 1'b0;
        for (memory_index = 0; memory_index < MEM_WORDS; memory_index = memory_index + 1)
            irom.mem[memory_index] = 32'b0;
        $readmemh(program_file, irom.mem);
        if ($value$plusargs("DATA=%s", data_file))
            $readmemh(data_file, dram.mem);

        if ($value$plusargs("WAVE=%s", wave_file)) begin
            $dumpfile(wave_file);
            $dumpvars(0, tb_core);
        end

        repeat (5) @(negedge clk);
        rst_n = 1'b1;
        fetch_enable = 1'b1;
    end

    // The completion response is accepted only if it survived redirect kill.
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            completion_seen <= 1'b0;
            fetch_enable <= 1'b0;
        end else if (completion_response_now) begin
            completion_seen <= 1'b1;
            fetch_enable <= 1'b0;
        end
    end

    always @(posedge clk) begin
        if (rst_n) begin
            if ((dbg_forward_a == 2'b10) && dut.idex_fire) cov_fwd_ex_a <= cov_fwd_ex_a + 1;
            if ((dbg_forward_b == 2'b10) && dut.idex_fire) cov_fwd_ex_b <= cov_fwd_ex_b + 1;
            if ((dbg_forward_a == 2'b01) && dut.idex_fire) cov_fwd_wb_a <= cov_fwd_wb_a + 1;
            if ((dbg_forward_b == 2'b01) && dut.idex_fire) cov_fwd_wb_b <= cov_fwd_wb_b + 1;
            if (dut.ID_EX_valid && dut.ID_EX_usesRs && dut.EX_MEM_valid &&
                dut.MEM_WB_valid && (dut.EX_MEM_regDest != 0) &&
                (dut.EX_MEM_regDest == dut.ID_EX_rs) &&
                (dut.MEM_WB_regDest == dut.ID_EX_rs) && (dbg_forward_a == 2'b10))
                cov_fwd_double_a <= cov_fwd_double_a + 1;
            if (dut.ID_EX_valid && dut.ID_EX_usesRt && dut.EX_MEM_valid &&
                dut.MEM_WB_valid && (dut.EX_MEM_regDest != 0) &&
                (dut.EX_MEM_regDest == dut.ID_EX_rt) &&
                (dut.MEM_WB_regDest == dut.ID_EX_rt) && (dbg_forward_b == 2'b10))
                cov_fwd_double_b <= cov_fwd_double_b + 1;
            if (dbg_forward_blocked) cov_fwd_blocked <= cov_fwd_blocked + 1;
            if (dbg_load_hazard) cov_load_hazard <= cov_load_hazard + 1;
            if (dbg_wb_id_bypass_rs) cov_wb_id_rs <= cov_wb_id_rs + 1;
            if (dbg_wb_id_bypass_rt) cov_wb_id_rt <= cov_wb_id_rt + 1;
            if (ib_req_v && !ib_req_r) cov_ibus_backpressure <= cov_ibus_backpressure + 1;
            if (db_req_v && !db_req_r) cov_dbus_backpressure <= cov_dbus_backpressure + 1;
            if (ib_resp_v && !ib_resp_r) cov_ibus_resp_hold <= cov_ibus_resp_hold + 1;
            if (db_resp_v && !db_resp_r) cov_dbus_resp_hold <= cov_dbus_resp_hold + 1;
            if (im_resp_v && !im_resp_r) cov_ibus_mem_resp_hold <= cov_ibus_mem_resp_hold + 1;
            if (dm_resp_v && !dm_resp_r) cov_dbus_mem_resp_hold <= cov_dbus_mem_resp_hold + 1;
            if (dbg_redirect) cov_redirect <= cov_redirect + 1;
            if (dbg_redirect && ib_req_v && !ib_req_r)
                cov_redirect_ibus_blocked <= cov_redirect_ibus_blocked + 1;
            if (dbg_mispredict) cov_mispredict <= cov_mispredict + 1;
            if (dbg_branch_resolve && dut.branch_taken) cov_branch_taken <= cov_branch_taken + 1;
            if (dbg_branch_resolve && !dut.branch_taken) cov_branch_not_taken <= cov_branch_not_taken + 1;
            if (dbg_fetch_discard) cov_fetch_discard <= cov_fetch_discard + 1;
            if (dbg_exmem_wait) cov_exmem_wait <= cov_exmem_wait + 1;
            if (dut.MEM_WB_valid && dut.MEM_WB_regWrite &&
                (dut.MEM_WB_regDest == 0) && !dut.MEM_WB_trap)
                cov_write_r0 <= cov_write_r0 + 1;
        end
    end

    always @(negedge clk) begin
        if (rst_n && !finished) begin
            cycle_count = cycle_count + 1;
            if (trace_valid) begin
                if (trace_count >= expected_events) begin
                    $display("TB_FAIL: extra retirement event pc=%08x instr=%08x", trace_pc, trace_instr);
                    $fatal(1);
                end
                $fwrite(trace_fd,
                    "%0d,%0d,%0d,%0d,%0d,%0d,%08x,%08x,%08x,%0d,%0d,%08x,%0d,%0d,%08x,%0d,%08x,%08x,%01x,%08x,%08x,%08x,%08x\n",
                    trace_epoch, trace_event_order, trace_retire_order,
                    trace_cycle, trace_uid, trace_trap, trace_pc, trace_instr,
                    trace_next_pc, trace_rd_we, trace_rd, trace_rd_data,
                    trace_mem_valid, trace_mem_write, trace_mem_addr,
                    trace_mem_valid ? memory_size_bytes(trace_mem_size) : 0,
                    trace_store_data,
                    trace_mem_wdata, trace_mem_wstrb, trace_mem_raw,
                    trace_mem_rdata, trace_cause, trace_epc);
                trace_count = trace_count + 1;
                if (trace_mem_valid && trace_mem_write) cov_store_retire = cov_store_retire + 1;
                if (trace_mem_valid && !trace_mem_write) cov_load_retire = cov_load_retire + 1;
                if (trace_trap) cov_trap = cov_trap + 1;
                if (trace_pc == completion_pc) begin
                    completion_seen = 1'b1;
                    fetch_enable = 1'b0;
                    verification_halt = 1'b1;
                end
            end

            if (completion_seen && (trace_count == expected_events) && core_idle &&
                ibus_shim_idle && dbus_shim_idle &&
                scoreboard_idle && (scoreboard_error_count == 0) &&
                (monitor_error_count == 0)) begin
                finished = 1'b1;
                write_final_state();
                $display("TB_COMPLETE: events=%0d cycles=%0d", trace_count, cycle_count);
                $finish;
            end

            if (cycle_count >= max_cycles) begin
                $display("TB_TIMEOUT: cycles=%0d events=%0d/%0d completion=%0d idle=%0d",
                         cycle_count, trace_count, expected_events, completion_seen, core_idle);
                $fatal(1);
            end
        end
    end

endmodule

`default_nettype wire
