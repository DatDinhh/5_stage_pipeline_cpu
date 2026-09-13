`timescale 1ns/1ps
`default_nettype none

// Icarus-compatible active safety monitors. All observed signals are explicit
// ports so this checker can be instantiated in every supported testbench.
module core_monitors (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [31:0] r0_value,
    input  wire        core_idle,

    input  wire        ibus_req_valid,
    input  wire        ibus_req_ready,
    input  wire [31:0] ibus_req_addr,
    input  wire        ibus_resp_valid,
    input  wire        ibus_resp_ready,
    input  wire [31:0] ibus_resp_rdata,

    input  wire        dbus_req_valid,
    input  wire        dbus_req_ready,
    input  wire [31:0] dbus_req_addr,
    input  wire [31:0] dbus_req_wdata,
    input  wire [3:0]  dbus_req_wstrb,
    input  wire        dbus_resp_valid,
    input  wire        dbus_resp_ready,
    input  wire [31:0] dbus_resp_rdata,

    input  wire        trace_valid,
    input  wire [63:0] trace_event_order,
    input  wire [63:0] trace_retire_order,
    input  wire [63:0] trace_uid,
    input  wire        trace_trap,
    input  wire        trace_rd_we,
    input  wire [4:0]  trace_rd,
    input  wire        trace_mem_valid,
    input  wire        trace_mem_write,
    input  wire [3:0]  trace_mem_wstrb,

    output reg  [31:0] error_count
);
    reg        ibus_req_held;
    reg [31:0] ibus_req_addr_held;
    reg        dbus_req_held;
    reg [31:0] dbus_req_addr_held;
    reg [31:0] dbus_req_wdata_held;
    reg [3:0]  dbus_req_wstrb_held;

    reg        ibus_resp_held;
    reg [31:0] ibus_resp_rdata_held;
    reg        dbus_resp_held;
    reg [31:0] dbus_resp_rdata_held;

    reg [63:0] expected_event_order;
    reg [63:0] expected_retire_order;
    reg        have_last_uid;
    reg [63:0] last_uid;

    task automatic fail;
        input [8*128-1:0] message;
        begin
            error_count <= error_count + 32'd1;
            $display("CORE_MONITOR ERROR at %0t: %0s", $time, message);
            $fatal(1);
        end
    endtask

    // Reset quiescence is checked on a clock edge after asynchronous DUT reset
    // updates have settled; sampling on the reset edge itself would race NBAs.
    always @(posedge clk) begin
        if (!rst_n) begin
            ibus_req_held         <= 1'b0;
            ibus_req_addr_held    <= 32'b0;
            dbus_req_held         <= 1'b0;
            dbus_req_addr_held    <= 32'b0;
            dbus_req_wdata_held   <= 32'b0;
            dbus_req_wstrb_held   <= 4'b0;
            ibus_resp_held        <= 1'b0;
            ibus_resp_rdata_held  <= 32'b0;
            dbus_resp_held        <= 1'b0;
            dbus_resp_rdata_held  <= 32'b0;
            expected_event_order  <= 64'b0;
            expected_retire_order <= 64'b0;
            have_last_uid         <= 1'b0;
            last_uid              <= 64'b0;
            error_count           <= 32'b0;

            if ((ibus_req_valid !== 1'b0) ||
                (ibus_resp_ready !== 1'b0) ||
                (dbus_req_valid !== 1'b0) ||
                (dbus_resp_ready !== 1'b0) ||
                (trace_valid !== 1'b0) ||
                (core_idle !== 1'b1)) begin
                $display("CORE_MONITOR DETAIL: reset req_i=%b ready_i_resp=%b req_d=%b ready_d_resp=%b trace=%b idle=%b",
                         ibus_req_valid, ibus_resp_ready, dbus_req_valid,
                         dbus_resp_ready, trace_valid, core_idle);
                fail("core outputs were not quiescent during reset");
            end
        end else begin
            if (r0_value !== 32'b0) begin
                $display("CORE_MONITOR DETAIL: r0=%h", r0_value);
                fail("architectural register zero changed");
            end

            // A request held without acceptance must keep valid and its full
            // payload stable until a later handshake.
            if (ibus_req_held &&
                ((ibus_req_valid !== 1'b1) ||
                 (ibus_req_addr !== ibus_req_addr_held))) begin
                $display("CORE_MONITOR DETAIL: IREQ expected_addr=%h valid=%b actual_addr=%h",
                         ibus_req_addr_held, ibus_req_valid, ibus_req_addr);
                fail("instruction request changed under backpressure");
            end
            if (dbus_req_held &&
                ((dbus_req_valid !== 1'b1) ||
                 (dbus_req_addr !== dbus_req_addr_held) ||
                 (dbus_req_wdata !== dbus_req_wdata_held) ||
                 (dbus_req_wstrb !== dbus_req_wstrb_held))) begin
                $display("CORE_MONITOR DETAIL: DREQ expected=%h/%h/%b actual=%h/%h/%b valid=%b",
                         dbus_req_addr_held, dbus_req_wdata_held,
                         dbus_req_wstrb_held, dbus_req_addr,
                         dbus_req_wdata, dbus_req_wstrb, dbus_req_valid);
                fail("data request changed under backpressure");
            end

            ibus_req_held <= ibus_req_valid && !ibus_req_ready;
            if (ibus_req_valid && !ibus_req_ready)
                ibus_req_addr_held <= ibus_req_addr;
            dbus_req_held <= dbus_req_valid && !dbus_req_ready;
            if (dbus_req_valid && !dbus_req_ready) begin
                dbus_req_addr_held  <= dbus_req_addr;
                dbus_req_wdata_held <= dbus_req_wdata;
                dbus_req_wstrb_held <= dbus_req_wstrb;
            end

            // A response under backpressure must retain both valid and data.
            if (ibus_resp_held &&
                ((ibus_resp_valid !== 1'b1) ||
                 (ibus_resp_rdata !== ibus_resp_rdata_held))) begin
                $display("CORE_MONITOR DETAIL: IRESP expected=%h valid=%b actual=%h",
                         ibus_resp_rdata_held, ibus_resp_valid,
                         ibus_resp_rdata);
                fail("instruction response changed under backpressure");
            end
            if (dbus_resp_held &&
                ((dbus_resp_valid !== 1'b1) ||
                 (dbus_resp_rdata !== dbus_resp_rdata_held))) begin
                $display("CORE_MONITOR DETAIL: DRESP expected=%h valid=%b actual=%h",
                         dbus_resp_rdata_held, dbus_resp_valid,
                         dbus_resp_rdata);
                fail("data response changed under backpressure");
            end

            ibus_resp_held <= ibus_resp_valid && !ibus_resp_ready;
            if (ibus_resp_valid && !ibus_resp_ready)
                ibus_resp_rdata_held <= ibus_resp_rdata;
            dbus_resp_held <= dbus_resp_valid && !dbus_resp_ready;
            if (dbus_resp_valid && !dbus_resp_ready)
                dbus_resp_rdata_held <= dbus_resp_rdata;

            if ((trace_valid !== 1'b0) && (trace_valid !== 1'b1))
                fail("trace_valid contains X/Z");

            if (trace_valid === 1'b1) begin
                if (trace_event_order !== expected_event_order) begin
                    $display("CORE_MONITOR DETAIL: expected_event=%0d actual_event=%0d uid=%0d",
                             expected_event_order, trace_event_order, trace_uid);
                    fail("trace event order is not contiguous");
                end
                if (trace_retire_order !== expected_retire_order) begin
                    $display("CORE_MONITOR DETAIL: expected_retire=%0d actual_retire=%0d uid=%0d trap=%b",
                             expected_retire_order, trace_retire_order,
                             trace_uid, trace_trap);
                    fail("trace retire order violates trap/retire policy");
                end
                if (^trace_uid === 1'bx)
                    fail("trace UID contains X/Z");
                if (have_last_uid && (trace_uid <= last_uid)) begin
                    $display("CORE_MONITOR DETAIL: previous_uid=%0d current_uid=%0d",
                             last_uid, trace_uid);
                    fail("trace UID did not strictly increase");
                end
                if ((trace_trap !== 1'b0) && (trace_trap !== 1'b1))
                    fail("trace_trap contains X/Z");
                if ((trace_rd_we !== 1'b0) && (trace_rd_we !== 1'b1))
                    fail("trace_rd_we contains X/Z");
                if ((trace_mem_valid !== 1'b0) && (trace_mem_valid !== 1'b1))
                    fail("trace_mem_valid contains X/Z");
                if ((trace_mem_write !== 1'b0) && (trace_mem_write !== 1'b1))
                    fail("trace_mem_write contains X/Z");

                if ((trace_trap === 1'b1) &&
                    ((trace_rd_we !== 1'b0) ||
                     (trace_mem_valid !== 1'b0) ||
                     (trace_mem_write !== 1'b0) ||
                     (trace_mem_wstrb !== 4'b0000))) begin
                    $display("CORE_MONITOR DETAIL: trap uid=%0d rd_we=%b mem_valid=%b mem_write=%b wstrb=%b",
                             trace_uid, trace_rd_we, trace_mem_valid,
                             trace_mem_write, trace_mem_wstrb);
                    fail("trap event carried an architectural side effect");
                end
                if ((trace_rd_we === 1'b1) && (trace_rd === 5'd0))
                    fail("trace attempted to write architectural register zero");
                if ((trace_mem_write === 1'b1) &&
                    (trace_mem_valid !== 1'b1))
                    fail("trace memory write was not marked memory-valid");

                expected_event_order <= expected_event_order + 64'd1;
                if (trace_trap === 1'b0)
                    expected_retire_order <= expected_retire_order + 64'd1;
                have_last_uid <= 1'b1;
                last_uid <= trace_uid;
            end
        end
    end
endmodule

`default_nettype wire
