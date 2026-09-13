`timescale 1ns/1ps

module rv_rom #(
    parameter integer BYTES       = 4096,
    parameter integer RANDOM_WAIT = 1,
    parameter integer WAIT_MAX    = 5,
    parameter integer FIXED_WAIT  = 0,
    parameter [31:0]  WAIT_SEED   = 32'h0000_00A5
)(
    input  wire        clk,
    input  wire        rst_n,
    input  wire        req_valid,
    output reg         req_ready,
    input  wire [31:0] req_addr,
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
        if ((BYTES < 36) || ((BYTES % 4) != 0)) begin
            $display("RV_ROM: BYTES must be a multiple of four and hold the built-in program");
            $fatal(1);
        end
        if ((WAIT_MAX < 0) || (FIXED_WAIT < 0)) begin
            $display("RV_ROM: wait parameters must be non-negative");
            $fatal(1);
        end

        for (i = 0; i < WORDS; i = i + 1)
            mem[i] = 32'h0000_0000;
        mem[0] = 32'h20010005;
        mem[1] = 32'h2002000A;
        mem[2] = 32'h00221820;
        mem[3] = 32'hAC030000;
        mem[4] = 32'h8C040000;
        mem[5] = 32'h20840001;
        mem[6] = 32'hAC040004;
        mem[7] = 32'h8C050004;
        mem[8] = 32'h10A0FFFB;
    end

    reg [31:0] lat_addr;
    reg [WAIT_WIDTH-1:0] wait_cnt;
    reg [31:0] lfsr;
    reg        busy;

    wire req_fire = req_valid && req_ready;
    wire [WAIT_WIDTH-1:0] selected_wait =
        RANDOM_WAIT ? (lfsr % (WAIT_MAX + 1)) : FIXED_WAIT;

    always @* begin
        // A consumed response may be replaced by a new request on the same
        // edge.  While a response is held, the slot remains exclusively owned.
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
        end else begin
            if (resp_valid && resp_ready)
                resp_valid <= 1'b0;

            if (req_fire) begin
                if ((^req_addr === 1'bx) || (req_addr > LAST_BYTE_ADDR)) begin
                    $display("RV_ROM: request address out of range: addr=%h bytes=%0d", req_addr, BYTES);
                    $fatal(1);
                end

                lat_addr <= {req_addr[31:2], 2'b00};
                wait_cnt <= selected_wait;
                lfsr     <= {lfsr[30:0], lfsr[31] ^ lfsr[21] ^ lfsr[1] ^ lfsr[0]};
                if (selected_wait == {WAIT_WIDTH{1'b0}}) begin
                    // Zero inserted waits: register the response directly from
                    // this accepted request.  This permits one transfer/cycle
                    // after fill while preserving a stable response register.
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
                    resp_rdata <= mem[lat_addr[31:2]];
                    resp_valid <= 1'b1;
                    busy       <= 1'b0;
                end
            end
        end
    end
endmodule
