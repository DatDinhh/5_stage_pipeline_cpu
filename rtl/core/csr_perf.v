`timescale 1ns/1ps
module csr_perf (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        stall_event,
    input  wire        flush_event,
    input  wire        retire_event,
    input  wire        trap_set,
    input  wire [31:0] epc_w,
    input  wire [31:0] cause_w,
    input  wire [31:0] addr,
    output reg  [31:0] rdata,
    output wire [31:0] epc_ro,
    output wire [31:0] cause_ro
);
    localparam CSR_BASE    = 32'hFFFF_F000;
    localparam CSR_CYCLELO = CSR_BASE + 32'h00;
    localparam CSR_CYCLEHI = CSR_BASE + 32'h04;
    localparam CSR_INSTRET = CSR_BASE + 32'h08;
    localparam CSR_STALL   = CSR_BASE + 32'h0C;
    localparam CSR_FLUSH   = CSR_BASE + 32'h10;
    localparam CSR_EPC     = CSR_BASE + 32'hF0;
    localparam CSR_CAUSE   = CSR_BASE + 32'hF4;

    reg [63:0] cycle;
    reg [63:0] instret;
    reg [31:0] stall_cycles;
    reg [31:0] flush_count;
    reg [31:0] epc, cause;

    assign epc_ro   = epc;
    assign cause_ro = cause;

    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            cycle<=0; instret<=0; stall_cycles<=0; flush_count<=0; epc<=0; cause<=0;
        end else begin
            cycle <= cycle + 64'd1;
            if (stall_event) stall_cycles <= stall_cycles + 32'd1;
            if (flush_event) flush_count  <= flush_count  + 32'd1;
            if (retire_event) instret     <= instret + 64'd1;
            if (trap_set) begin epc <= epc_w; cause <= cause_w; end
        end
    end

    always @* begin
        case (addr)
            CSR_CYCLELO: rdata = cycle[31:0];
            CSR_CYCLEHI: rdata = cycle[63:32];
            // The CSR load in EX/MEM is architecturally after the older
            // instruction currently retiring in MEM/WB on this edge.
            CSR_INSTRET: rdata = instret[31:0] + (retire_event ? 32'd1 : 32'd0);
            CSR_STALL  : rdata = stall_cycles;
            CSR_FLUSH  : rdata = flush_count;
            CSR_EPC    : rdata = epc;
            CSR_CAUSE  : rdata = cause;
            default    : rdata = 32'hDEAD_BEEF;
        endcase
    end
endmodule
