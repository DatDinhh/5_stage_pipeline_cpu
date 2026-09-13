`timescale 1ns/1ps
`default_nettype none

module tb_hazard;
    reg       producer_valid;
    reg       producer_is_load;
    reg [4:0] producer_rd;
    reg       consumer_valid;
    reg [4:0] consumer_rs;
    reg [4:0] consumer_rt;
    reg       consumer_uses_rs;
    reg       consumer_uses_rt;
    wire      stall;
    wire      pc_write;
    wire      if_id_write;
    wire      control_bubble;

    integer checks;
    integer errors;

    hazard_unit dut (
        .ID_EX_valid(producer_valid),
        .ID_EX_memRead(producer_is_load),
        .ID_EX_rt(producer_rd),
        .IF_ID_valid(consumer_valid),
        .IF_ID_rs(consumer_rs),
        .IF_ID_rt(consumer_rt),
        .IF_ID_usesRs(consumer_uses_rs),
        .IF_ID_usesRt(consumer_uses_rt),
        .stall(stall),
        .pcWrite(pc_write),
        .IF_ID_Write(if_id_write),
        .controlBubble(control_bubble)
    );

    task automatic set_idle;
        begin
            producer_valid   = 1'b0;
            producer_is_load = 1'b0;
            producer_rd      = 5'd0;
            consumer_valid   = 1'b1;
            consumer_rs      = 5'd5;
            consumer_rt      = 5'd6;
            consumer_uses_rs = 1'b1;
            consumer_uses_rt = 1'b1;
        end
    endtask

    task automatic check_stall;
        input expected_stall;
        begin
            #1;
            checks = checks + 1;
            if ((stall !== expected_stall) ||
                (pc_write !== ~expected_stall) ||
                (if_id_write !== ~expected_stall) ||
                (control_bubble !== expected_stall)) begin
                errors = errors + 1;
                $display("FAIL hazard check %0d: got stall=%b pcWrite=%b IF_ID_Write=%b bubble=%b; expected stall=%b",
                         checks, stall, pc_write, if_id_write, control_bubble,
                         expected_stall);
            end
        end
    endtask

    initial begin
        checks = 0;
        errors = 0;

        set_idle();
        check_stall(1'b0);

        // A valid load destination consumed as rs must stall.
        set_idle();
        producer_valid = 1'b1; producer_is_load = 1'b1; producer_rd = 5'd5;
        check_stall(1'b1);

        // Store/branch-style rt consumption is also a dependency.
        set_idle();
        producer_valid = 1'b1; producer_is_load = 1'b1; producer_rd = 5'd6;
        check_stall(1'b1);

        // An encoded register field that the instruction does not use is ignored.
        set_idle();
        producer_valid = 1'b1; producer_is_load = 1'b1; producer_rd = 5'd5;
        consumer_uses_rs = 1'b0;
        check_stall(1'b0);

        set_idle();
        producer_valid = 1'b1; producer_is_load = 1'b1; producer_rd = 5'd6;
        consumer_uses_rt = 1'b0;
        check_stall(1'b0);

        // Producer and consumer valid bits qualify the dependency.
        set_idle();
        producer_valid = 1'b0; producer_is_load = 1'b1; producer_rd = 5'd5;
        check_stall(1'b0);

        set_idle();
        producer_valid = 1'b1; producer_is_load = 1'b1; producer_rd = 5'd5;
        consumer_valid = 1'b0;
        check_stall(1'b0);

        // An ALU writer and a load to r0 never create this stall.
        set_idle();
        producer_valid = 1'b1; producer_is_load = 1'b0; producer_rd = 5'd5;
        check_stall(1'b0);

        set_idle();
        producer_valid = 1'b1; producer_is_load = 1'b1; producer_rd = 5'd0;
        consumer_rs = 5'd0; consumer_rt = 5'd0;
        check_stall(1'b0);

        // A nonmatching valid load does not stall.
        set_idle();
        producer_valid = 1'b1; producer_is_load = 1'b1; producer_rd = 5'd9;
        check_stall(1'b0);

        if (errors != 0) begin
            $fatal(1, "tb_hazard: %0d of %0d checks failed", errors, checks);
        end
        $display("PASS tb_hazard: %0d checks", checks);
        $finish;
    end
endmodule

`default_nettype wire
