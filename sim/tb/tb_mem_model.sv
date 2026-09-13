`timescale 1ns/1ps
`default_nettype none

module tb_mem_model;
    localparam integer MEM_BYTES = 64;
    localparam integer MEM_FIXED_WAIT = 17;
    localparam integer ROM_WAIT_MAX = 3;
    localparam [31:0]  ROM_WAIT_SEED = 32'h0000_00A5;

    reg clk = 1'b0;
    reg rst_n = 1'b0;
    always #5 clk = ~clk;

    reg         d_req_valid = 1'b0;
    wire        d_req_ready;
    reg  [31:0] d_req_addr = 32'b0;
    reg  [31:0] d_req_wdata = 32'b0;
    reg  [3:0]  d_req_wstrb = 4'b0;
    wire        d_resp_valid;
    reg         d_resp_ready = 1'b0;
    wire [31:0] d_resp_rdata;

    reg         i_req_valid = 1'b0;
    wire        i_req_ready;
    reg  [31:0] i_req_addr = 32'b0;
    wire        i_resp_valid;
    reg         i_resp_ready = 1'b0;
    wire [31:0] i_resp_rdata;

    integer d_accept_count = 0;
    integer d_response_count = 0;
    integer d_store_create_count = 0;
    integer i_accept_count = 0;
    integer i_response_count = 0;
    integer wait_cycles;
    reg [31:0] held_response;

    rv_mem #(
        .BYTES(MEM_BYTES),
        .RANDOM_WAIT(0),
        .WAIT_MAX(37),
        .FIXED_WAIT(MEM_FIXED_WAIT),
        .WAIT_SEED(32'h1234_5678)
    ) dmem (
        .clk(clk),
        .rst_n(rst_n),
        .req_valid(d_req_valid),
        .req_ready(d_req_ready),
        .req_addr(d_req_addr),
        .req_wdata(d_req_wdata),
        .req_wstrb(d_req_wstrb),
        .resp_valid(d_resp_valid),
        .resp_ready(d_resp_ready),
        .resp_rdata(d_resp_rdata)
    );

    rv_rom #(
        .BYTES(MEM_BYTES),
        .RANDOM_WAIT(1),
        .WAIT_MAX(ROM_WAIT_MAX),
        .FIXED_WAIT(0),
        .WAIT_SEED(ROM_WAIT_SEED)
    ) irom (
        .clk(clk),
        .rst_n(rst_n),
        .req_valid(i_req_valid),
        .req_ready(i_req_ready),
        .req_addr(i_req_addr),
        .resp_valid(i_resp_valid),
        .resp_ready(i_resp_ready),
        .resp_rdata(i_resp_rdata)
    );

    task automatic check_true;
        input condition;
        input [8*120-1:0] message;
        begin
            if (!condition) begin
                $display("TB_MEM_MODEL FAIL: %0s", message);
                $fatal(1);
            end
        end
    endtask

    always @(posedge clk) begin
        if (rst_n) begin
            if (d_req_valid && d_req_ready)
                d_accept_count <= d_accept_count + 1;
            if (d_resp_valid && d_resp_ready)
                d_response_count <= d_response_count + 1;
            if (dmem.busy && (dmem.wait_cnt == 0) && (|dmem.lat_wstrb))
                d_store_create_count <= d_store_create_count + 1;
            if (i_req_valid && i_req_ready)
                i_accept_count <= i_accept_count + 1;
            if (i_resp_valid && i_resp_ready)
                i_response_count <= i_response_count + 1;
        end
    end

    initial begin
        repeat (1000) @(posedge clk);
        $display("TB_MEM_MODEL FAIL: timeout");
        $fatal(1);
    end

    initial begin
        repeat (3) @(negedge clk);
        rst_n = 1'b1;
        @(negedge clk);
        check_true(d_req_ready && i_req_ready, "models were not ready after reset release");
        check_true($bits(dmem.wait_cnt) >= 6, "data wait counter was too narrow for WAIT_MAX=37");

        if ($test$plusargs("OUT_OF_RANGE")) begin
            d_req_addr  = MEM_BYTES;
            d_req_wdata = 32'hDEAD_BEEF;
            d_req_wstrb = 4'hF;
            d_req_valid = 1'b1;
            @(posedge clk);
            @(negedge clk);
            $display("TB_MEM_MODEL FAIL: out-of-range request did not terminate simulation");
            $fatal(1);
        end

        // Keep valid high with a second payload after the first handshake.
        // The occupied one-entry model must not over-accept it.
        d_req_addr   = 32'h0000_0000;
        d_req_wdata  = 32'h1122_3344;
        d_req_wstrb  = 4'hF;
        d_req_valid  = 1'b1;
        d_resp_ready = 1'b0;
        @(posedge clk);
        @(negedge clk);
        check_true(d_accept_count == 1, "first data request was not accepted exactly once");
        check_true(!d_req_ready, "data request ready stayed high after acceptance");

        d_req_addr  = 32'h0000_0004;
        d_req_wdata = 32'hDEAD_BEEF;
        d_req_wstrb = 4'hF;

        wait_cycles = 0;
        while (!d_resp_valid) begin
            @(posedge clk);
            wait_cycles = wait_cycles + 1;
            @(negedge clk);
            check_true(d_accept_count == 1, "continuous valid over-accepted while request was waiting");
            check_true(!d_req_ready, "data request ready asserted while request was waiting");
        end
        check_true(wait_cycles == (MEM_FIXED_WAIT + 1), "fixed wait response latency was not deterministic");
        check_true(dmem.mem[0] === 32'h1122_3344, "latched full-word store payload was corrupted");
        check_true(dmem.mem[1] === 32'h0000_0000, "unaccepted continuous-valid payload modified memory");
        check_true(d_store_create_count == 1, "full-word store was not created exactly once");

        held_response = d_resp_rdata;
        repeat (3) begin
            @(posedge clk);
            @(negedge clk);
            check_true(d_resp_valid, "held data response valid dropped");
            check_true(d_resp_rdata === held_response, "held data response payload changed");
            check_true(!d_req_ready, "data request accepted while response was held");
            check_true(d_accept_count == 1, "continuous valid over-accepted while response was held");
            check_true(d_store_create_count == 1, "store repeated while response was held");
        end

        d_resp_ready = 1'b1;
        @(posedge clk);
        @(negedge clk);
        check_true(!d_resp_valid && !d_req_ready,
                   "turnover request did not take ownership after response consumption");
        check_true(d_accept_count == 2,
                   "data model did not accept exactly one turnover request");
        d_req_valid  = 1'b0;
        d_resp_ready = 1'b0;

        wait_cycles = 0;
        while (!d_resp_valid) begin
            @(posedge clk);
            wait_cycles = wait_cycles + 1;
            @(negedge clk);
        end
        check_true(wait_cycles == (MEM_FIXED_WAIT + 1),
                   "turnover request used the wrong fixed wait");
        check_true(dmem.mem[1] === 32'hDEAD_BEEF,
                   "turnover request payload was not preserved");
        check_true(d_store_create_count == 2,
                   "turnover store was not created exactly once");
        d_resp_ready = 1'b1;
        @(posedge clk);
        @(negedge clk);
        d_resp_ready = 1'b0;

        // Byte strobes update only their selected lane. Holding the store
        // response must not repeat the side effect.
        d_req_addr   = 32'h0000_0001;
        d_req_wdata  = 32'h0000_AA00;
        d_req_wstrb  = 4'b0010;
        d_req_valid  = 1'b1;
        @(posedge clk);
        @(negedge clk);
        d_req_valid = 1'b0;
        wait_cycles = 0;
        while (!d_resp_valid) begin
            @(posedge clk);
            wait_cycles = wait_cycles + 1;
            @(negedge clk);
        end
        check_true(wait_cycles == (MEM_FIXED_WAIT + 1), "byte store used the wrong fixed wait");
        check_true(dmem.mem[0] === 32'h1122_AA44, "byte strobe did not preserve neighboring lanes");
        check_true(d_store_create_count == 3, "byte store was not created exactly once");
        held_response = d_resp_rdata;
        repeat (3) begin
            @(posedge clk);
            @(negedge clk);
            check_true(d_resp_valid && (d_resp_rdata === held_response), "byte-store response was not stable");
            check_true(dmem.mem[0] === 32'h1122_AA44, "byte store repeated while response was held");
            check_true(d_store_create_count == 3, "byte store generated multiple completion events");
        end
        d_resp_ready = 1'b1;
        @(posedge clk);
        @(negedge clk);
        d_resp_ready = 1'b0;

        // A read returns the merged word and obeys the same fixed latency.
        d_req_addr   = 32'h0000_0000;
        d_req_wdata  = 32'b0;
        d_req_wstrb  = 4'b0000;
        d_req_valid  = 1'b1;
        @(posedge clk);
        @(negedge clk);
        d_req_valid = 1'b0;
        wait_cycles = 0;
        while (!d_resp_valid) begin
            @(posedge clk);
            wait_cycles = wait_cycles + 1;
            @(negedge clk);
        end
        check_true(wait_cycles == (MEM_FIXED_WAIT + 1), "data read used the wrong fixed wait");
        check_true(d_resp_rdata === 32'h1122_AA44, "data read did not return the merged word");
        d_resp_ready = 1'b1;
        @(posedge clk);
        @(negedge clk);
        d_resp_ready = 1'b0;

        // The seeded ROM sequence is deterministic: A5 % 4 gives one extra
        // wait, then 0x14B % 4 gives three extra waits.
        i_req_addr   = 32'h0000_0000;
        i_req_valid  = 1'b1;
        i_resp_ready = 1'b0;
        @(posedge clk);
        @(negedge clk);
        i_req_addr = 32'h0000_0004;
        wait_cycles = 0;
        while (!i_resp_valid) begin
            @(posedge clk);
            wait_cycles = wait_cycles + 1;
            @(negedge clk);
            check_true(i_accept_count == 1, "ROM continuous valid over-accepted");
            check_true(!i_req_ready, "ROM request ready asserted while occupied");
        end
        check_true(wait_cycles == 2, "first seeded ROM wait was not deterministic");
        check_true(i_resp_rdata === 32'h2001_0005, "ROM returned the wrong first instruction");
        held_response = i_resp_rdata;
        repeat (2) begin
            @(posedge clk);
            @(negedge clk);
            check_true(i_resp_valid && (i_resp_rdata === held_response), "held ROM response changed");
            check_true(i_accept_count == 1, "ROM accepted while its response was held");
        end
        i_resp_ready = 1'b1;
        @(posedge clk);
        @(negedge clk);
        check_true(!i_resp_valid && !i_req_ready,
                   "ROM turnover request did not take ownership");
        check_true(i_accept_count == 2,
                   "ROM did not accept exactly one turnover request");
        i_req_valid  = 1'b0;
        i_resp_ready = 1'b0;

        wait_cycles = 0;
        while (!i_resp_valid) begin
            @(posedge clk);
            wait_cycles = wait_cycles + 1;
            @(negedge clk);
        end
        check_true(wait_cycles == 4, "second seeded ROM wait was not deterministic");
        check_true(i_resp_rdata === 32'h2002_000A, "ROM returned the wrong second instruction");
        i_resp_ready = 1'b1;
        @(posedge clk);
        @(negedge clk);
        i_resp_ready = 1'b0;

        // Reset aborts an accepted transaction while retaining memory.
        d_req_addr  = 32'h0000_0000;
        d_req_wstrb = 4'b0000;
        d_req_valid = 1'b1;
        @(posedge clk);
        @(negedge clk);
        d_req_valid = 1'b0;
        rst_n = 1'b0;
        repeat (2) @(negedge clk);
        check_true(!d_req_ready && !d_resp_valid, "protocol state was not quiescent in reset");
        check_true(dmem.mem[0] === 32'h1122_AA44, "warm reset cleared data memory");
        rst_n = 1'b1;
        repeat (MEM_FIXED_WAIT + 2) begin
            @(posedge clk);
            @(negedge clk);
            check_true(!d_resp_valid, "aborted request produced a response after reset");
        end
        check_true(d_req_ready && i_req_ready, "models did not return to idle after warm reset");
        check_true(d_accept_count == 5, "unexpected total number of data requests accepted");
        check_true(d_response_count == 4, "unexpected total number of data responses consumed");
        check_true(d_store_create_count == 3, "unexpected total number of stores created");
        check_true(i_accept_count == 2, "unexpected total number of ROM requests accepted");
        check_true(i_response_count == 2, "unexpected total number of ROM responses consumed");

        $display("TB_MEM_MODEL PASS: ready/valid, waits, backpressure, strobes, reset, and range guard");
        $finish;
    end

endmodule

`default_nettype wire
