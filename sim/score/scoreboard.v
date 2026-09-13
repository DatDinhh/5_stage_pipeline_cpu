`timescale 1ns/1ps
`default_nettype none

// Protocol-to-retirement correlation checker. This module deliberately does
// not model architectural memory contents; the independent ISS owns that job.
module scoreboard #(
    parameter [31:0] CSR_BASE_MASK = 32'hFFFF_FF00,
    parameter [31:0] CSR_BASE      = 32'hFFFF_F000
)(
    input  wire        clk,
    input  wire        rst_n,

    input  wire        dbus_req_valid,
    input  wire        dbus_req_ready,
    input  wire [31:0] dbus_req_addr,
    input  wire [31:0] dbus_req_wdata,
    input  wire [3:0]  dbus_req_wstrb,

    input  wire        dbus_resp_valid,
    input  wire        dbus_resp_ready,
    input  wire [31:0] dbus_resp_rdata,

    input  wire        trace_valid,
    input  wire [63:0] trace_uid,
    input  wire        trace_trap,
    input  wire        trace_mem_valid,
    input  wire        trace_mem_write,
    input  wire [31:0] trace_mem_addr,
    input  wire [1:0]  trace_mem_size,
    input  wire [31:0] trace_mem_wdata,
    input  wire [3:0]  trace_mem_wstrb,
    input  wire [31:0] trace_mem_rdata,

    output wire        idle,
    output reg  [63:0] request_count,
    output reg  [63:0] response_count,
    output reg  [63:0] store_count,
    output reg  [31:0] error_count
);
    wire req_fire = dbus_req_valid && dbus_req_ready;
    wire resp_fire = dbus_resp_valid && dbus_resp_ready;
    // Architectural CSR reads use the trace memory fields but never traverse
    // the external data bus, so they are outside this correlation checker.
    wire trace_is_csr = ((trace_mem_addr & CSR_BASE_MASK) == CSR_BASE);
    wire trace_mem_fire = trace_valid && trace_mem_valid && !trace_is_csr;

    // Accepted request that has not received its response.
    reg        request_pending;
    reg [31:0] request_addr;
    reg [31:0] request_wdata;
    reg [3:0]  request_wstrb;
    reg        request_is_store;

    // Responded request waiting for its later registered trace event.
    reg        completion_pending;
    reg [31:0] completion_addr;
    reg [31:0] completion_wdata;
    reg [3:0]  completion_wstrb;
    reg        completion_is_store;
    reg [31:0] completion_resp_rdata;

    assign idle = !request_pending && !completion_pending;

    task automatic fail;
        input [8*128-1:0] message;
        begin
            error_count <= error_count + 32'd1;
            $display("SCOREBOARD ERROR at %0t: %0s", $time, message);
            $fatal(1);
        end
    endtask

    // One sequential owner handles request acceptance, response transfer,
    // trace comparison, counters, and simultaneous consume/refill cases.
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            request_pending      <= 1'b0;
            request_addr         <= 32'b0;
            request_wdata        <= 32'b0;
            request_wstrb        <= 4'b0;
            request_is_store     <= 1'b0;
            completion_pending   <= 1'b0;
            completion_addr      <= 32'b0;
            completion_wdata     <= 32'b0;
            completion_wstrb     <= 4'b0;
            completion_is_store  <= 1'b0;
            completion_resp_rdata<= 32'b0;
            request_count        <= 64'b0;
            response_count       <= 64'b0;
            store_count          <= 64'b0;
            error_count          <= 32'b0;
        end else begin
            if (req_fire) begin
                if ((^dbus_req_addr === 1'bx) ||
                    (^dbus_req_wstrb === 1'bx)) begin
                    fail("request address or strobe contains X/Z");
                end
                if ((|dbus_req_wstrb) && (^dbus_req_wdata === 1'bx)) begin
                    fail("store payload contains X/Z");
                end
                if (request_pending && !resp_fire) begin
                    fail("request accepted while another request is outstanding");
                end
            end

            if (resp_fire && !request_pending) begin
                fail("response consumed without an owned request");
            end
            if (resp_fire && completion_pending && !trace_mem_fire) begin
                fail("response arrived while the completion slot was occupied");
            end
            if (trace_mem_fire && !completion_pending) begin
                fail("memory trace event has no completed bus transaction");
            end

            if (trace_mem_fire && completion_pending) begin
                if (trace_trap !== 1'b0) begin
                    $display("SCOREBOARD DETAIL: uid=%0d trace_trap=%b", trace_uid, trace_trap);
                    fail("external memory completion retired as a trap");
                end
                if (trace_mem_write !== completion_is_store) begin
                    $display("SCOREBOARD DETAIL: uid=%0d expected_write=%b actual_write=%b",
                             trace_uid, completion_is_store, trace_mem_write);
                    fail("memory operation kind mismatch");
                end
                if (trace_mem_addr !== completion_addr) begin
                    $display("SCOREBOARD DETAIL: uid=%0d expected_addr=%h actual_addr=%h",
                             trace_uid, completion_addr, trace_mem_addr);
                    fail("memory effective address mismatch");
                end
                if (completion_is_store) begin
                    if (trace_mem_wdata !== completion_wdata) begin
                        $display("SCOREBOARD DETAIL: uid=%0d size=%b expected_wdata=%h actual_wdata=%h",
                                 trace_uid, trace_mem_size, completion_wdata, trace_mem_wdata);
                        fail("store payload mismatch");
                    end
                    if (trace_mem_wstrb !== completion_wstrb) begin
                        $display("SCOREBOARD DETAIL: uid=%0d expected_wstrb=%b actual_wstrb=%b",
                                 trace_uid, completion_wstrb, trace_mem_wstrb);
                        fail("store strobe mismatch");
                    end
                end else if (trace_mem_wstrb !== 4'b0000) begin
                    $display("SCOREBOARD DETAIL: uid=%0d load_wstrb=%b response_word=%h trace_load=%h",
                             trace_uid, trace_mem_wstrb,
                             completion_resp_rdata, trace_mem_rdata);
                    fail("load trace carries a nonzero write strobe");
                end
            end

            if (req_fire) begin
                request_count <= request_count + 64'd1;
                if (|dbus_req_wstrb)
                    store_count <= store_count + 64'd1;
            end
            if (resp_fire)
                response_count <= response_count + 64'd1;

            // A response may consume the old request on the same edge that a
            // new request is accepted. Nonblocking assignments preserve the
            // old request record for the response-to-completion transfer.
            case ({req_fire, resp_fire})
                2'b10: begin
                    request_pending  <= 1'b1;
                    request_addr     <= dbus_req_addr;
                    request_wdata    <= dbus_req_wdata;
                    request_wstrb    <= dbus_req_wstrb;
                    request_is_store <= |dbus_req_wstrb;
                end
                2'b01: begin
                    request_pending <= 1'b0;
                end
                2'b11: begin
                    request_pending  <= 1'b1;
                    request_addr     <= dbus_req_addr;
                    request_wdata    <= dbus_req_wdata;
                    request_wstrb    <= dbus_req_wstrb;
                    request_is_store <= |dbus_req_wstrb;
                end
            endcase

            // Comparing an older completion and receiving the next response
            // may coincide. In that case the completion slot is atomically
            // refilled from the still-visible old request record.
            case ({resp_fire, trace_mem_fire})
                2'b10, 2'b11: begin
                    completion_pending    <= 1'b1;
                    completion_addr       <= request_addr;
                    completion_wdata      <= request_wdata;
                    completion_wstrb      <= request_wstrb;
                    completion_is_store   <= request_is_store;
                    completion_resp_rdata <= dbus_resp_rdata;
                end
                2'b01: begin
                    completion_pending <= 1'b0;
                end
            endcase
        end
    end
endmodule

`default_nettype wire
