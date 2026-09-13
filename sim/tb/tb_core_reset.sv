`timescale 1ns/1ps
`default_nettype none

// Full-core warm reset under a shared core/interconnect/slave reset contract.
// A store already made visible by rv_mem when its response is created survives
// reset; an accepted store still waiting for response creation is cancelled.
// No stale response is injected after reset: the untagged bus requires slaves
// to cancel their old ownership on the same reset as the core.
module tb_core_reset;
    localparam integer MEM_BYTES = 4096;
    localparam integer WORDS = MEM_BYTES / 4;
    localparam integer SCENARIOS = 9;
    localparam integer MEMORY_WAIT = 12;
    reg clk = 1'b0;
    reg rst_n = 1'b1;
    reg fetch_enable = 1'b0;
    reg pre_reset = 1'b0;
    reg recovery = 1'b0;
    integer scenario = 0;
    always #5 clk = ~clk;

    wire ib_req_v, ib_req_r, ib_mem_ready, ib_resp_v, ib_resp_r;
    wire [31:0] ib_req_a, ib_resp_d;
    wire db_req_v, db_req_r, db_mem_ready, db_resp_v, db_resp_r;
    wire [31:0] db_req_a, db_req_wd, db_resp_d;
    wire [3:0] db_req_ws;
    wire core_idle;
    // Gate valid and ready symmetrically, so each accepted request is seen by
    // both the real slave and the core. No internal DUT signals are forced.
    wire ib_allow = !(pre_reset && (scenario == 0) && (ib_req_a == 32'd12));
    wire db_allow = !(pre_reset && ((scenario == 3) || (scenario == 6)));
    assign ib_req_r = ib_allow && ib_mem_ready;
    assign db_req_r = db_allow && db_mem_ready;

    cpu_core #(.DMEM_BYTES(MEM_BYTES)) dut (
        .clk(clk), .rst_n(rst_n),
        .ibus_req_valid(ib_req_v), .ibus_req_ready(ib_req_r),
        .ibus_req_addr(ib_req_a), .ibus_resp_valid(ib_resp_v),
        .ibus_resp_ready(ib_resp_r), .ibus_resp_rdata(ib_resp_d),
        .dbus_req_valid(db_req_v), .dbus_req_ready(db_req_r),
        .dbus_req_addr(db_req_a), .dbus_req_wdata(db_req_wd),
        .dbus_req_wstrb(db_req_ws), .dbus_resp_valid(db_resp_v),
        .dbus_resp_ready(db_resp_r), .dbus_resp_rdata(db_resp_d),
        .fetch_enable(fetch_enable), .verification_halt(1'b0),
        .core_idle(core_idle)
    );
    rv_rom #(.BYTES(MEM_BYTES), .RANDOM_WAIT(0), .WAIT_MAX(MEMORY_WAIT),
             .FIXED_WAIT(MEMORY_WAIT)) irom (
        .clk(clk), .rst_n(rst_n), .req_valid(ib_req_v && ib_allow),
        .req_ready(ib_mem_ready), .req_addr(ib_req_a),
        .resp_valid(ib_resp_v), .resp_ready(ib_resp_r), .resp_rdata(ib_resp_d)
    );
    rv_mem #(.BYTES(MEM_BYTES), .RANDOM_WAIT(0), .WAIT_MAX(MEMORY_WAIT),
             .FIXED_WAIT(MEMORY_WAIT)) dram (
        .clk(clk), .rst_n(rst_n), .req_valid(db_req_v && db_allow),
        .req_ready(db_mem_ready), .req_addr(db_req_a),
        .req_wdata(db_req_wd), .req_wstrb(db_req_ws),
        .resp_valid(db_resp_v), .resp_ready(db_resp_r), .resp_rdata(db_resp_d)
    );

    wire scoreboard_idle;
    wire [63:0] request_count, response_count, store_count;
    wire [31:0] scoreboard_errors, monitor_errors;
    scoreboard u_scoreboard (
        .clk(clk), .rst_n(rst_n),
        .dbus_req_valid(db_req_v), .dbus_req_ready(db_req_r),
        .dbus_req_addr(db_req_a), .dbus_req_wdata(db_req_wd),
        .dbus_req_wstrb(db_req_ws), .dbus_resp_valid(db_resp_v),
        .dbus_resp_ready(db_resp_r), .dbus_resp_rdata(db_resp_d),
        .trace_valid(dut.trace_valid), .trace_uid(dut.trace_uid),
        .trace_trap(dut.trace_trap), .trace_mem_valid(dut.trace_mem_valid),
        .trace_mem_write(dut.trace_mem_write), .trace_mem_addr(dut.trace_mem_addr),
        .trace_mem_size(dut.trace_mem_size), .trace_mem_wdata(dut.trace_mem_wdata),
        .trace_mem_wstrb(dut.trace_mem_wstrb), .trace_mem_rdata(dut.trace_mem_rdata),
        .idle(scoreboard_idle), .request_count(request_count),
        .response_count(response_count), .store_count(store_count),
        .error_count(scoreboard_errors)
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
        .trace_valid(dut.trace_valid), .trace_event_order(dut.trace_event_order),
        .trace_retire_order(dut.trace_retire_order), .trace_uid(dut.trace_uid),
        .trace_trap(dut.trace_trap), .trace_rd_we(dut.trace_rd_we),
        .trace_rd(dut.trace_rd), .trace_mem_valid(dut.trace_mem_valid),
        .trace_mem_write(dut.trace_mem_write), .trace_mem_wstrb(dut.trace_mem_wstrb),
        .error_count(monitor_errors)
    );

    integer hits [0:SCENARIOS-1];
    integer ownership_clear = 0;
    integer registers_clear = 0;
    integer no_ghost_retire = 0;
    integer no_ghost_store = 0;
    integer memory_persistence = 0;
    integer restart_vector = 0;
    integer restart_complete = 0;
    integer warm_activity = 0;
    integer pre_events = 0;
    integer post_events = 0;
    integer post_ireqs = 0;
    integer omit_scenario = -1;
    integer coverage_fd;
    string coverage_file;
    reg [31:0] expected_old_store;

    task automatic fail(input string message);
        begin
            $display("CORE_RESET_FAIL: scenario=%0d time=%0t %0s", scenario, $time, message);
            $fatal(1);
        end
    endtask
    task automatic tick;
        begin
            @(negedge clk);
            #1;
        end
    endtask
    function automatic string scenario_bin(input integer number);
        case (number)
            0: scenario_bin = "reset_ibus_request_pending";
            1: scenario_bin = "reset_ibus_response_wait";
            2: scenario_bin = "reset_ibus_response_offered";
            3: scenario_bin = "reset_load_request_pending";
            4: scenario_bin = "reset_load_response_wait";
            5: scenario_bin = "reset_load_response_offered";
            6: scenario_bin = "reset_store_request_pending";
            7: scenario_bin = "reset_store_response_wait";
            8: scenario_bin = "reset_store_response_offered";
            default: scenario_bin = "invalid";
        endcase
    endfunction
    function automatic target_reached(input integer number);
        case (number)
            0: target_reached = ib_req_v && !ib_req_r && (ib_req_a == 32'd12);
            1: target_reached = dut.if_outstanding && irom.busy && !ib_resp_v &&
                                (dut.if_outstanding_pc == 32'd12);
            2: target_reached = dut.if_outstanding && ib_resp_v &&
                                (dut.if_outstanding_pc == 32'd12);
            3: target_reached = db_req_v && !db_req_r && (db_req_ws == 4'b0);
            4: target_reached = dut.EX_MEM_mem_accepted && dram.busy &&
                                !db_resp_v && !dut.EX_MEM_memWrite;
            5: target_reached = dut.EX_MEM_mem_accepted && db_resp_v &&
                                !dut.EX_MEM_memWrite;
            6: target_reached = db_req_v && !db_req_r && (db_req_ws == 4'hf);
            7: target_reached = dut.EX_MEM_mem_accepted && dram.busy &&
                                !db_resp_v && dut.EX_MEM_memWrite;
            8: target_reached = dut.EX_MEM_mem_accepted && db_resp_v &&
                                dut.EX_MEM_memWrite;
            default: target_reached = 1'b0;
        endcase
    endfunction

    function automatic [31:0] restart_instruction(input integer event_number);
        case (event_number)
            0: restart_instruction = 32'h2008_2468; // addi r8,r0,0x2468
            1: restart_instruction = 32'h8c09_0100; // lw r9,0x100(r0)
            2: restart_instruction = 32'hac08_0108; // sw r8,0x108(r0)
            3: restart_instruction = 32'h212a_0001; // addi r10,r9,1
            4: restart_instruction = 32'h200b_6789; // addi r11,r0,0x6789
            default: restart_instruction = 32'hffff_ffff;
        endcase
    endfunction

    task automatic check_memory(input bit completed_restart_store);
        integer word_index;
        reg [31:0] expected_word;
        begin
            for (word_index = 0; word_index < WORDS; word_index = word_index + 1) begin
                expected_word = 32'b0;
                if (word_index == 64) expected_word = 32'h1122_3344;
                if (word_index == 65) expected_word = expected_old_store;
                if ((word_index == 66) && completed_restart_store)
                    expected_word = 32'h0000_2468;
                if (dram.mem[word_index] !== expected_word) begin
                    $display("CORE_RESET_MEMORY: addr=%08x expected=%08x actual=%08x",
                             word_index * 4, expected_word, dram.mem[word_index]);
                    fail("memory changed outside the permitted completed store");
                end
            end
        end
    endtask

    task automatic check_reset_ownership;
        begin
            if ((ib_req_v !== 1'b0) || (ib_resp_r !== 1'b0) ||
                (db_req_v !== 1'b0) || (db_resp_r !== 1'b0) ||
                (ib_resp_v !== 1'b0) || (db_resp_v !== 1'b0) ||
                (dut.trace_valid !== 1'b0) || (core_idle !== 1'b1) ||
                (scoreboard_idle !== 1'b1) || (irom.busy !== 1'b0) ||
                (dram.busy !== 1'b0) || (dut.if_req_present !== 1'b0) ||
                (dut.if_outstanding !== 1'b0) || (dut.if_buf_valid !== 1'b0) ||
                (dut.EX_MEM_mem_accepted !== 1'b0) ||
                (dut.IF_ID_valid !== 1'b0) || (dut.ID_EX_valid !== 1'b0) ||
                (dut.EX_MEM_valid !== 1'b0) || (dut.MEM_WB_valid !== 1'b0))
                fail("reset did not clear all core/slave/scoreboard ownership");
        end
    endtask
    task automatic check_zero_registers;
        integer register_index;
        begin
            for (register_index = 0; register_index < 32; register_index = register_index + 1)
                if (dut.u_rf.rf[register_index] !== 32'b0)
                    fail("architectural register survived reset or ghost writeback occurred");
        end
    endtask

    // Count and check every fetched restart instruction at its real handshake.
    always @(posedge clk) begin
        if (rst_n && recovery && ib_req_v && ib_req_r) begin
            if (ib_req_a !== (post_ireqs * 4))
                fail("restart fetch sequence did not begin at reset vector and advance once");
            if (post_ireqs >= 5) fail("extra restart instruction request");
            if (post_ireqs == 0) restart_vector = restart_vector + 1;
            post_ireqs = post_ireqs + 1;
        end
    end

    task automatic check_restart_event;
        reg expected_we;
        reg [4:0] expected_rd;
        reg [31:0] expected_value;
        begin
            if (post_events >= 5) fail("ghost or duplicate retirement after restart");
            expected_we = (post_events != 2);
            case (post_events)
                0: begin expected_rd = 5'd8; expected_value = 32'h0000_2468; end
                1: begin expected_rd = 5'd9; expected_value = 32'h1122_3344; end
                2: begin expected_rd = 5'd0; expected_value = 32'b0; end
                3: begin expected_rd = 5'd10; expected_value = 32'h1122_3345; end
                default: begin expected_rd = 5'd11; expected_value = 32'h0000_6789; end
            endcase
            if ((dut.trace_pc !== (post_events * 4)) ||
                (dut.trace_instr !== restart_instruction(post_events)) ||
                (dut.trace_next_pc !== ((post_events + 1) * 4)) ||
                (dut.trace_event_order !== post_events) ||
                (dut.trace_retire_order !== post_events) ||
                (dut.trace_uid !== post_events) || (dut.trace_epoch !== 32'b0) ||
                (dut.trace_trap !== 1'b0) || (dut.trace_cause !== 32'b0) ||
                (dut.trace_epc !== 32'b0) || (dut.trace_rd_we !== expected_we) ||
                (dut.trace_rd !== expected_rd) || (dut.trace_rd_data !== expected_value))
                fail("restart retirement differs from exact expected instruction/register/order");
            if ((dut.trace_mem_valid !== ((post_events == 1) || (post_events == 2))) ||
                (dut.trace_mem_write !== (post_events == 2)))
                fail("restart memory event flags mismatch");
            if (post_events == 1) begin
                if ((dut.trace_mem_addr !== 32'h100) || (dut.trace_mem_size !== 2'b00) ||
                    (dut.trace_mem_raw !== 32'h1122_3344) ||
                    (dut.trace_mem_rdata !== 32'h1122_3344) ||
                    (dut.trace_mem_wstrb !== 4'b0))
                    fail("restart load data/address/size mismatch");
            end else if (post_events == 2) begin
                if ((dut.trace_mem_addr !== 32'h108) || (dut.trace_mem_size !== 2'b00) ||
                    (dut.trace_store_data !== 32'h2468) ||
                    (dut.trace_mem_wdata !== 32'h2468) || (dut.trace_mem_wstrb !== 4'hf))
                    fail("restart store data/address/size/strobe mismatch");
            end
            post_events = post_events + 1;
        end
    endtask

    task automatic run_scenario;
        integer index;
        integer timeout_count;
        reg [31:0] expected_register;
        begin
            // Establish a fresh pre-reset workload; memory initialization is
            // test setup only and is never performed during the warm reset.
            tick();
            rst_n = 1'b0;
            fetch_enable = 1'b0;
            recovery = 1'b0;
            pre_reset = 1'b0;
            pre_events = 0;
            post_events = 0;
            post_ireqs = 0;
            expected_old_store = 32'hdead_beef;
            #1;
            for (index = 0; index < WORDS; index = index + 1) begin
                irom.mem[index] = 32'b0;
                dram.mem[index] = 32'b0;
            end
            dram.mem[64] = 32'h1122_3344;
            dram.mem[65] = 32'hdead_beef;
            irom.mem[0] = 32'h2001_0100; // addi r1,r0,0x100
            irom.mem[1] = 32'h2002_1234; // addi r2,r0,0x1234
            irom.mem[2] = 32'h2006_5555; // addi r6,r0,0x5555
            irom.mem[3] = (scenario >= 6) ? 32'hac22_0004 : 32'h8c23_0000;
            irom.mem[4] = 32'h2007_7bad; // must never survive the warm reset
            repeat (3) tick();
            pre_reset = 1'b1;
            rst_n = 1'b1;
            fetch_enable = 1'b1;
            timeout_count = 0;
            while (!target_reached(scenario)) begin
                tick();
                timeout_count = timeout_count + 1;
                if (dut.trace_valid) begin
                    if ((dut.trace_trap !== 1'b0) || (dut.trace_mem_valid !== 1'b0) ||
                        (dut.trace_pc !== (pre_events * 4)) ||
                        (dut.trace_instr !== irom.mem[pre_events]))
                        fail("unexpected retirement before reset boundary");
                    pre_events = pre_events + 1;
                end
                if (timeout_count > 400) fail("target reset phase was never reached");
            end
            // The request/wait phases must persist for several real edges.
            // Offered responses are reset before the next consuming edge.
            if ((scenario % 3) != 2) begin
                repeat (3) begin
                    tick();
                    if (!target_reached(scenario)) fail("pending reset phase did not remain pending");
                    if (dut.trace_valid) begin
                        if (dut.trace_mem_valid || dut.trace_trap)
                            fail("pending memory transaction retired before reset");
                        pre_events = pre_events + 1;
                    end
                end
            end
            if ((pre_events < 2) || (dut.u_rf.rf[1] !== 32'h100) ||
                (dut.u_rf.rf[2] !== 32'h1234))
                fail("reset was not warm: prior architectural work did not execute");
            warm_activity = warm_activity + 1;
            if (scenario >= 3) begin
                if ((dut.EX_MEM_pc !== 32'd12) || (dut.EX_MEM_trap !== 1'b0) ||
                    (dut.EX_MEM_memWrite !== (scenario >= 6)))
                    fail("reset targeted the wrong full-core memory instruction");
                if ((scenario % 3) == 0) begin
                    if (request_count !== 64'd0) fail("blocked request was accepted");
                end else if (request_count !== 64'd1)
                    fail("pending transaction was not accepted exactly once");
                if (response_count !== 64'd0) fail("offered response was consumed before reset");
            end
            // This store already became externally visible when its response
            // was created. Reset must preserve it, but must never replay it.
            if (scenario == 8) expected_old_store = 32'h0000_1234;
            check_memory(1'b0);
            rst_n = 1'b0;
            fetch_enable = 1'b0;
            pre_reset = 1'b0;
            #1;
            check_reset_ownership();
            check_zero_registers();
            repeat (3) begin
                tick();
                check_reset_ownership();
                check_zero_registers();
                check_memory(1'b0);
            end
            ownership_clear = ownership_clear + 1;
            registers_clear = registers_clear + 1;
            for (index = 0; index < WORDS; index = index + 1) irom.mem[index] = 32'b0;
            for (index = 0; index < 5; index = index + 1)
                irom.mem[index] = restart_instruction(index);
            rst_n = 1'b1;
            // Wait longer than the cancelled slave latency with fetch off.
            // Old requests/responses or late stores cannot hide in restart.
            repeat (MEMORY_WAIT + 8) begin
                tick();
                check_reset_ownership();
                check_zero_registers();
                check_memory(1'b0);
            end
            no_ghost_retire = no_ghost_retire + 1;
            no_ghost_store = no_ghost_store + 1;
            memory_persistence = memory_persistence + 1;
            recovery = 1'b1;
            fetch_enable = 1'b1;
            timeout_count = 0;
            while ((post_events != 5) || !core_idle || !scoreboard_idle) begin
                tick();
                if (dut.if_outstanding && (dut.if_outstanding_pc == 32'd16))
                    fetch_enable = 1'b0;
                if (dut.trace_valid) check_restart_event();
                timeout_count = timeout_count + 1;
                if (timeout_count > 600) fail("restart did not complete and drain");
            end
            repeat (MEMORY_WAIT + 8) begin
                tick();
                if (dut.trace_valid || ib_req_v || db_req_v || !core_idle || !scoreboard_idle)
                    fail("ghost retirement or request after restart drained");
                check_memory(1'b1);
            end
            if ((post_ireqs != 5) || (request_count !== 64'd2) ||
                (response_count !== 64'd2) || (store_count !== 64'd1) ||
                (scoreboard_errors !== 32'd0) || (monitor_errors !== 32'd0))
                fail("restart bus counts, scoreboard, or monitor checks failed");
            for (index = 0; index < 32; index = index + 1) begin
                expected_register = 32'b0;
                case (index)
                    8: expected_register = 32'h2468;
                    9: expected_register = 32'h1122_3344;
                    10: expected_register = 32'h1122_3345;
                    11: expected_register = 32'h6789;
                endcase
                if (dut.u_rf.rf[index] !== expected_register)
                    fail("full register state after restart mismatch");
            end
            hits[scenario] = hits[scenario] + 1;
            restart_complete = restart_complete + 1;
            recovery = 1'b0;
            $display("CORE_RESET_SCENARIO_PASS: %0s pre_events=%0d restart_events=%0d",
                     scenario_bin(scenario), pre_events, post_events);
        end
    endtask

    initial begin
        #1 rst_n = 1'b0;
        for (scenario = 0; scenario < SCENARIOS; scenario = scenario + 1) hits[scenario] = 0;
        if (!$value$plusargs("COVERAGE=%s", coverage_file))
            fail("missing +COVERAGE=<path>");
        if ($value$plusargs("OMIT_SCENARIO=%d", omit_scenario)) begin
            if ((omit_scenario < 0) || (omit_scenario >= SCENARIOS))
                fail("OMIT_SCENARIO must select 0 through 8");
        end
        coverage_fd = $fopen(coverage_file, "w");
        if (coverage_fd == 0) fail("cannot open coverage output");
        for (scenario = 0; scenario < SCENARIOS; scenario = scenario + 1)
            if (scenario != omit_scenario) run_scenario();
        for (scenario = 0; scenario < SCENARIOS; scenario = scenario + 1)
            $fwrite(coverage_fd, "%0s=%0d\n", scenario_bin(scenario), hits[scenario]);
        $fwrite(coverage_fd, "reset_ownership_clear=%0d\n", ownership_clear);
        $fwrite(coverage_fd, "reset_registers_clear=%0d\n", registers_clear);
        $fwrite(coverage_fd, "reset_no_ghost_retire=%0d\n", no_ghost_retire);
        $fwrite(coverage_fd, "reset_no_ghost_store=%0d\n", no_ghost_store);
        $fwrite(coverage_fd, "reset_memory_persistence=%0d\n", memory_persistence);
        $fwrite(coverage_fd, "reset_restart_vector=%0d\n", restart_vector);
        $fwrite(coverage_fd, "reset_restart_complete=%0d\n", restart_complete);
        $fwrite(coverage_fd, "reset_warm_activity=%0d\n", warm_activity);
        $fclose(coverage_fd);
        for (scenario = 0; scenario < SCENARIOS; scenario = scenario + 1)
            if (hits[scenario] != 1)
                fail($sformatf("required warm-reset scenario coverage missing: %0s", scenario_bin(scenario)));
        if ((ownership_clear != SCENARIOS) || (registers_clear != SCENARIOS) ||
            (no_ghost_retire != SCENARIOS) || (no_ghost_store != SCENARIOS) ||
            (memory_persistence != SCENARIOS) || (restart_vector != SCENARIOS) ||
            (restart_complete != SCENARIOS) || (warm_activity != SCENARIOS))
            fail("required reset recovery coverage missing");
        $display("CORE_RESET_PASS: scenarios=%0d restart_events=%0d", SCENARIOS, SCENARIOS * 5);
        $finish;
    end
    initial begin
        #200000;
        fail("global watchdog timeout");
    end
endmodule

`default_nettype wire
