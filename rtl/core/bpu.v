`timescale 1ns/1ps
module bpu #(
    parameter BHT_ENTRIES=64,
    parameter BTB_ENTRIES=32
)(
    input  wire        clk,
    input  wire        rst_n,
    input  wire [31:0] if_pc,
    output wire        pred_taken,
    output wire [31:0] pred_target,
    output wire        pred_hit,
    input  wire        update_en,
    input  wire [31:0] update_pc,
    input  wire        update_taken,
    input  wire [31:0] update_target
);
    // Entry counts must be positive. Modulo indexing also keeps non-power-of-two
    // parameter values in range; the default powers of two synthesize to masks.
    localparam BHTW = (BHT_ENTRIES > 1) ? $clog2(BHT_ENTRIES) : 1;
    reg [1:0] bht[BHT_ENTRIES-1:0];
    integer i;
    wire [31:0] bht_mod_if = (if_pc >> 2) % BHT_ENTRIES;
    wire [31:0] bht_mod_up = (update_pc >> 2) % BHT_ENTRIES;
    wire [BHTW-1:0] bht_idx_if = bht_mod_if[BHTW-1:0];
    wire [BHTW-1:0] bht_idx_up = bht_mod_up[BHTW-1:0];
    assign pred_taken = bht[bht_idx_if][1];

    localparam BTBW = (BTB_ENTRIES > 1) ? $clog2(BTB_ENTRIES) : 1;
    reg [31:0] btb_target[BTB_ENTRIES-1:0];
    reg [31:0] btb_tag[BTB_ENTRIES-1:0];
    reg        btb_valid[BTB_ENTRIES-1:0];
    integer j;
    wire [31:0] btb_mod_if = (if_pc >> 2) % BTB_ENTRIES;
    wire [31:0] btb_mod_up = (update_pc >> 2) % BTB_ENTRIES;
    wire [BTBW-1:0] btb_idx_if = btb_mod_if[BTBW-1:0];
    wire [31:0]     tag_if     = if_pc;
    wire [BTBW-1:0] btb_idx_up = btb_mod_up[BTBW-1:0];
    wire [31:0]     tag_up     = update_pc;

    assign pred_target = btb_target[btb_idx_if];
    assign pred_hit    = btb_valid[btb_idx_if] & (btb_tag[btb_idx_if]==tag_if);

    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            for (i=0;i<BHT_ENTRIES;i=i+1) begin
                bht[i] <= 2'b01;
            end
            for (j=0;j<BTB_ENTRIES;j=j+1) begin
                btb_target[j] <= 32'b0;
                btb_tag[j]    <= 32'b0;
                btb_valid[j]  <= 1'b0;
            end
        end else begin
            if(update_en) begin
                case ({update_taken, bht[bht_idx_up]})
                    3'b1_00: bht[bht_idx_up]<=2'b01;
                    3'b1_01: bht[bht_idx_up]<=2'b10;
                    3'b1_10: bht[bht_idx_up]<=2'b11;
                    3'b1_11: bht[bht_idx_up]<=2'b11;
                    3'b0_00: bht[bht_idx_up]<=2'b00;
                    3'b0_01: bht[bht_idx_up]<=2'b00;
                    3'b0_10: bht[bht_idx_up]<=2'b01;
                    3'b0_11: bht[bht_idx_up]<=2'b10;
                endcase
            end
            if(update_en && update_taken) begin
                btb_valid[btb_idx_up]  <= 1'b1;
                btb_target[btb_idx_up] <= update_target;
                btb_tag[btb_idx_up]    <= tag_up;
            end
        end
    end
endmodule
