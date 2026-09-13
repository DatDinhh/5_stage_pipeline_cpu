`timescale 1ns/1ps

module rv_mem #(
    parameter integer BYTES       = 4096,
    parameter integer RANDOM_WAIT = 1,
    parameter integer WAIT_MAX    = 12,
    parameter integer FIXED_WAIT  = 0,
    parameter [31:0]  WAIT_SEED   = 32'h0000_00A5
)(
    input  wire        clk,
    input  wire        rst_n,
    input  wire        req_valid,
    output reg         req_ready,
    input  wire [31:0] req_addr,
    input  wire [31:0] req_wdata,
    input  wire [3:0]  req_wstrb,
    output reg         resp_valid,
    input  wire        resp_ready,
    output reg  [31:0] resp_rdata
);
    localparam integer WORDS = BYTES / 4;
    localparam integer WAIT_LIMIT = (WAIT_MAX > FIXED_WAIT) ? WAIT_MAX : FIXED_WAIT;
    localparam integer WAIT_WIDTH = (WAIT_LIMIT < 1) ? 1 : $clog2(WAIT_LIMIT + 1);
    localparam [31:0] LAST_BYTE_ADDR = BYTES - 1;

    reg [31:0] mem [0:WORDS-1];
    integer i;

    initial begin
        if ((BYTES < 4) || ((BYTES % 4) != 0)) begin
            $display("RV_MEM: BYTES must be a positive multiple of four");
            $fatal(1);
        end
        if ((WAIT_MAX < 0) || (FIXED_WAIT < 0)) begin
            $display("RV_MEM: wait parameters must be non-negative");
            $fatal(1);
        end
        for (i = 0; i < WORDS; i = i + 1)
            mem[i] = 32'h0000_0000;
    end

    reg [31:0] lat_addr;
    reg [31:0] lat_wdata;
    reg [3:0]  lat_wstrb;
    reg [WAIT_WIDTH-1:0] wait_cnt;
    reg [31:0] lfsr;
    reg        busy;

    wire req_fire = req_valid && req_ready;
    wire [WAIT_WIDTH-1:0] selected_wait =
        RANDOM_WAIT ? (lfsr % (WAIT_MAX + 1)) : FIXED_WAIT;

    // The single entry remains occupied through response consumption.  A new
    // request may replace a response only on the edge that consumes it.
    always @* begin
        req_ready = rst_n && !busy && (!resp_valid || resp_ready);
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            resp_valid <= 1'b0;
            resp_rdata <= 32'b0;
            busy       <= 1'b0;
            wait_cnt   <= {WAIT_WIDTH{1'b0}};
            lfsr       <= (WAIT_SEED == 32'b0) ? 32'h0000_0001 : WAIT_SEED;
            lat_addr   <= 32'b0;
            lat_wdata  <= 32'b0;
            lat_wstrb  <= 4'b0000;
        end else begin
            if (resp_valid && resp_ready)
                resp_valid <= 1'b0;

            if (req_fire) begin
                if ((^req_addr === 1'bx) || (req_addr > LAST_BYTE_ADDR)) begin
                    $display("RV_MEM: request address out of range: addr=%h bytes=%0d", req_addr, BYTES);
                    $fatal(1);
                end

                lat_addr  <= {req_addr[31:2], 2'b00};
                lat_wdata <= req_wdata;
                lat_wstrb <= req_wstrb;
                wait_cnt  <= selected_wait;
                lfsr      <= {lfsr[30:0], lfsr[31] ^ lfsr[21] ^ lfsr[1] ^ lfsr[0]};
                if (selected_wait == {WAIT_WIDTH{1'b0}}) begin
                    if (req_wstrb[0]) mem[req_addr[31:2]][7:0]   <= req_wdata[7:0];
                    if (req_wstrb[1]) mem[req_addr[31:2]][15:8]  <= req_wdata[15:8];
                    if (req_wstrb[2]) mem[req_addr[31:2]][23:16] <= req_wdata[23:16];
                    if (req_wstrb[3]) mem[req_addr[31:2]][31:24] <= req_wdata[31:24];
                    resp_rdata <= mem[req_addr[31:2]];
                    resp_valid <= 1'b1;
                    busy       <= 1'b0;
                end else begin
                    busy       <= 1'b1;
                end
            end else if (busy) begin
                if (wait_cnt != {WAIT_WIDTH{1'b0}}) begin
                    wait_cnt <= wait_cnt - 1'b1;
                end else begin
                    // A store becomes visible exactly once, when its response
                    // is created. Memory contents intentionally survive reset.
                    if (lat_wstrb[0]) mem[lat_addr[31:2]][7:0]   <= lat_wdata[7:0];
                    if (lat_wstrb[1]) mem[lat_addr[31:2]][15:8]  <= lat_wdata[15:8];
                    if (lat_wstrb[2]) mem[lat_addr[31:2]][23:16] <= lat_wdata[23:16];
                    if (lat_wstrb[3]) mem[lat_addr[31:2]][31:24] <= lat_wdata[31:24];

                    resp_rdata <= mem[lat_addr[31:2]];
                    resp_valid <= 1'b1;
                    busy       <= 1'b0;
                end
            end
        end
    end
endmodule
