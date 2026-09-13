`timescale 1ns/1ps
`default_nettype none

// A legal, single-outstanding adversarial link. The request is accepted by
// both endpoints on the same edge, after REQUEST_STALL_CYCLES blocked cycles.
// A memory response is held at the memory-facing interface before being
// exposed to the core. Once exposed it cannot be withdrawn before acceptance.
// In particular this does not force the core's response-ready output low.
module rv_bus_shim #(
    parameter integer REQUEST_STALL_CYCLES = 2,
    parameter integer RESPONSE_HOLD_CYCLES = 2
)(
    input wire clk,
    input wire rst_n,
    input wire s_req_valid,
    output wire s_req_ready,
    input wire [31:0] s_req_addr,
    input wire [31:0] s_req_wdata,
    input wire [3:0] s_req_wstrb,
    output wire s_resp_valid,
    input wire s_resp_ready,
    output wire [31:0] s_resp_rdata,
    output wire m_req_valid,
    input wire m_req_ready,
    output wire [31:0] m_req_addr,
    output wire [31:0] m_req_wdata,
    output wire [3:0] m_req_wstrb,
    input wire m_resp_valid,
    output wire m_resp_ready,
    input wire [31:0] m_resp_rdata,
    output wire idle
);
    integer request_remaining;
    integer response_remaining;
    reg outstanding;
    reg memory_response_held;
    reg [31:0] held_response_data;
    reg memory_request_held;
    reg [67:0] held_request_payload;
    reg [63:0] request_count;
    reg [63:0] response_count;

    wire request_open = !outstanding && (request_remaining == 0);
    wire response_open = outstanding && (response_remaining == 0);
    wire request_fire = s_req_valid && s_req_ready;
    wire response_fire = s_resp_valid && s_resp_ready;

    assign s_req_ready = rst_n && request_open && m_req_ready;
    assign m_req_valid = rst_n && request_open && s_req_valid;
    assign m_req_addr = s_req_addr;
    assign m_req_wdata = s_req_wdata;
    assign m_req_wstrb = s_req_wstrb;
    assign s_resp_valid = rst_n && response_open && m_resp_valid;
    assign s_resp_rdata = m_resp_rdata;
    assign m_resp_ready = rst_n && response_open && s_resp_ready;
    assign idle = !outstanding && !s_req_valid && !m_resp_valid;

    initial begin
        if ((REQUEST_STALL_CYCLES < 0) || (RESPONSE_HOLD_CYCLES < 0))
            $fatal(1, "BUS_SHIM_CONFIG_FAIL: negative stall/hold count");
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            request_remaining <= REQUEST_STALL_CYCLES;
            response_remaining <= RESPONSE_HOLD_CYCLES;
            outstanding <= 1'b0;
            memory_response_held <= 1'b0;
            held_response_data <= 32'b0;
            memory_request_held <= 1'b0;
            held_request_payload <= 68'b0;
            request_count <= 64'b0;
            response_count <= 64'b0;
        end else begin
            if (memory_response_held &&
                ((m_resp_valid !== 1'b1) ||
                 (m_resp_rdata !== held_response_data)))
                $fatal(1, "BUS_SHIM_FAIL: memory response changed while held");
            if (memory_request_held &&
                ((m_req_valid !== 1'b1) ||
                 ({m_req_addr, m_req_wdata, m_req_wstrb} !== held_request_payload)))
                $fatal(1, "BUS_SHIM_FAIL: memory request changed while held");
            if (m_resp_valid && !outstanding)
                $fatal(1, "BUS_SHIM_FAIL: unowned memory response");
            if (request_fire !== (m_req_valid && m_req_ready))
                $fatal(1, "BUS_SHIM_FAIL: request handshake mismatch");
            if (response_fire !== (m_resp_valid && m_resp_ready))
                $fatal(1, "BUS_SHIM_FAIL: response handshake mismatch");
            if ((response_count > request_count) ||
                ((request_count - response_count) != (outstanding ? 64'd1 : 64'd0)))
                $fatal(1, "BUS_SHIM_FAIL: transaction ownership/count mismatch");

            memory_response_held <= m_resp_valid && !m_resp_ready;
            if (m_resp_valid && !m_resp_ready)
                held_response_data <= m_resp_rdata;
            memory_request_held <= m_req_valid && !m_req_ready;
            if (m_req_valid && !m_req_ready)
                held_request_payload <= {m_req_addr, m_req_wdata, m_req_wstrb};

            if (!outstanding && s_req_valid && (request_remaining > 0))
                request_remaining <= request_remaining - 1;
            if (outstanding && m_resp_valid && (response_remaining > 0))
                response_remaining <= response_remaining - 1;
            if (request_fire) begin
                outstanding <= 1'b1;
                request_count <= request_count + 64'd1;
                request_remaining <= REQUEST_STALL_CYCLES;
                response_remaining <= RESPONSE_HOLD_CYCLES;
            end
            if (response_fire) begin
                outstanding <= 1'b0;
                response_count <= response_count + 64'd1;
            end
        end
    end
endmodule

`default_nettype wire
