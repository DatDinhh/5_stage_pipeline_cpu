`timescale 1ns/1ps
`default_nettype none

module tb_forwarding;
    reg        ex_valid;
    reg        ex_reg_write;
    reg        ex_result_ready;
    reg [4:0]  ex_rd;
    reg        wb_valid;
    reg        wb_reg_write;
    reg [4:0]  wb_rd;
    reg        consumer_valid;
    reg        uses_rs;
    reg        uses_rt;
    reg [4:0]  rs;
    reg [4:0]  rt;
    wire [1:0] forward_a;
    wire [1:0] forward_b;
    wire       blocked_a;
    wire       blocked_b;
    wire       blocked;

    integer checks;
    integer errors;

    forwarding_unit dut (
        .EX_MEM_valid(ex_valid),
        .EX_MEM_regWrite(ex_reg_write),
        .EX_MEM_resultReady(ex_result_ready),
        .EX_MEM_rd(ex_rd),
        .MEM_WB_valid(wb_valid),
        .MEM_WB_regWrite(wb_reg_write),
        .MEM_WB_rd(wb_rd),
        .ID_EX_valid(consumer_valid),
        .ID_EX_usesRs(uses_rs),
        .ID_EX_usesRt(uses_rt),
        .ID_EX_rs(rs),
        .ID_EX_rt(rt),
        .forwardA(forward_a),
        .forwardB(forward_b),
        .blockedA(blocked_a),
        .blockedB(blocked_b),
        .blocked(blocked)
    );

    task automatic set_idle;
        begin
            ex_valid        = 1'b0;
            ex_reg_write    = 1'b0;
            ex_result_ready = 1'b0;
            ex_rd           = 5'd0;
            wb_valid        = 1'b0;
            wb_reg_write    = 1'b0;
            wb_rd           = 5'd0;
            consumer_valid  = 1'b1;
            uses_rs         = 1'b1;
            uses_rt         = 1'b1;
            rs              = 5'd5;
            rt              = 5'd6;
        end
    endtask

    task automatic check_outputs;
        input [1:0] expected_a;
        input [1:0] expected_b;
        input       expected_blocked_a;
        input       expected_blocked_b;
        begin
            #1;
            checks = checks + 1;
            if ((forward_a !== expected_a) ||
                (forward_b !== expected_b) ||
                (blocked_a !== expected_blocked_a) ||
                (blocked_b !== expected_blocked_b) ||
                (blocked !== (expected_blocked_a | expected_blocked_b))) begin
                errors = errors + 1;
                $display("FAIL forwarding check %0d: got A=%b B=%b blockA=%b blockB=%b block=%b; expected A=%b B=%b blockA=%b blockB=%b",
                         checks, forward_a, forward_b, blocked_a, blocked_b, blocked,
                         expected_a, expected_b, expected_blocked_a, expected_blocked_b);
            end
        end
    endtask

    initial begin
        checks = 0;
        errors = 0;

        set_idle();
        check_outputs(2'b00, 2'b00, 1'b0, 1'b0);

        // A ready young producer is selected.
        set_idle();
        ex_valid = 1'b1; ex_reg_write = 1'b1; ex_result_ready = 1'b1; ex_rd = 5'd5;
        check_outputs(2'b10, 2'b00, 1'b0, 1'b0);

        // An older WB producer is selected when no younger match exists.
        set_idle();
        wb_valid = 1'b1; wb_reg_write = 1'b1; wb_rd = 5'd6;
        check_outputs(2'b00, 2'b01, 1'b0, 1'b0);

        // Both operands may independently select the same young producer.
        set_idle();
        rs = 5'd7; rt = 5'd7;
        ex_valid = 1'b1; ex_reg_write = 1'b1; ex_result_ready = 1'b1; ex_rd = 5'd7;
        check_outputs(2'b10, 2'b10, 1'b0, 1'b0);

        // Double match: the younger EX/MEM writer has priority over WB.
        set_idle();
        ex_valid = 1'b1; ex_reg_write = 1'b1; ex_result_ready = 1'b1; ex_rd = 5'd5;
        wb_valid = 1'b1; wb_reg_write = 1'b1; wb_rd = 5'd5;
        check_outputs(2'b10, 2'b00, 1'b0, 1'b0);

        // A matching young unready producer blocks; it must not fall back to WB.
        set_idle();
        ex_valid = 1'b1; ex_reg_write = 1'b1; ex_result_ready = 1'b0; ex_rd = 5'd5;
        wb_valid = 1'b1; wb_reg_write = 1'b1; wb_rd = 5'd5;
        check_outputs(2'b00, 2'b00, 1'b1, 1'b0);

        // Blocking and forwarding are independent for the two operands.
        set_idle();
        ex_valid = 1'b1; ex_reg_write = 1'b1; ex_result_ready = 1'b0; ex_rd = 5'd5;
        wb_valid = 1'b1; wb_reg_write = 1'b1; wb_rd = 5'd6;
        check_outputs(2'b00, 2'b01, 1'b1, 1'b0);

        // An invalid young producer cannot shadow a valid WB producer.
        set_idle();
        ex_valid = 1'b0; ex_reg_write = 1'b1; ex_result_ready = 1'b0; ex_rd = 5'd5;
        wb_valid = 1'b1; wb_reg_write = 1'b1; wb_rd = 5'd5;
        check_outputs(2'b01, 2'b00, 1'b0, 1'b0);

        // Register zero is never a producer.
        set_idle();
        rs = 5'd0; rt = 5'd0;
        ex_valid = 1'b1; ex_reg_write = 1'b1; ex_result_ready = 1'b0; ex_rd = 5'd0;
        wb_valid = 1'b1; wb_reg_write = 1'b1; wb_rd = 5'd0;
        check_outputs(2'b00, 2'b00, 1'b0, 1'b0);

        // Invalid consumers and unused sources neither forward nor block.
        set_idle();
        consumer_valid = 1'b0;
        ex_valid = 1'b1; ex_reg_write = 1'b1; ex_result_ready = 1'b0; ex_rd = 5'd5;
        wb_valid = 1'b1; wb_reg_write = 1'b1; wb_rd = 5'd6;
        check_outputs(2'b00, 2'b00, 1'b0, 1'b0);

        set_idle();
        uses_rs = 1'b0; uses_rt = 1'b0;
        ex_valid = 1'b1; ex_reg_write = 1'b1; ex_result_ready = 1'b0; ex_rd = 5'd5;
        wb_valid = 1'b1; wb_reg_write = 1'b1; wb_rd = 5'd6;
        check_outputs(2'b00, 2'b00, 1'b0, 1'b0);

        if (errors != 0) begin
            $fatal(1, "tb_forwarding: %0d of %0d checks failed", errors, checks);
        end
        $display("PASS tb_forwarding: %0d checks", checks);
        $finish;
    end
endmodule

`default_nettype wire
