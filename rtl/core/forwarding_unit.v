`timescale 1ns/1ps
module forwarding_unit (
    input  wire       EX_MEM_valid,
    input  wire       EX_MEM_regWrite,
    input  wire       EX_MEM_resultReady,
    input  wire [4:0] EX_MEM_rd,
    input  wire       MEM_WB_valid,
    input  wire       MEM_WB_regWrite,
    input  wire [4:0] MEM_WB_rd,
    input  wire       ID_EX_valid,
    input  wire       ID_EX_usesRs,
    input  wire       ID_EX_usesRt,
    input  wire [4:0] ID_EX_rs,
    input  wire [4:0] ID_EX_rt,
    output reg [1:0]  forwardA,
    output reg [1:0]  forwardB,
    output reg        blockedA,
    output reg        blockedB,
    output wire       blocked
);
    wire ex_match_a = ID_EX_valid && ID_EX_usesRs &&
                      EX_MEM_valid && EX_MEM_regWrite && (EX_MEM_rd != 5'd0) &&
                      (EX_MEM_rd == ID_EX_rs);
    wire ex_match_b = ID_EX_valid && ID_EX_usesRt &&
                      EX_MEM_valid && EX_MEM_regWrite && (EX_MEM_rd != 5'd0) &&
                      (EX_MEM_rd == ID_EX_rt);
    wire wb_match_a = ID_EX_valid && ID_EX_usesRs &&
                      MEM_WB_valid && MEM_WB_regWrite && (MEM_WB_rd != 5'd0) &&
                      (MEM_WB_rd == ID_EX_rs);
    wire wb_match_b = ID_EX_valid && ID_EX_usesRt &&
                      MEM_WB_valid && MEM_WB_regWrite && (MEM_WB_rd != 5'd0) &&
                      (MEM_WB_rd == ID_EX_rt);

    assign blocked = blockedA | blockedB;

    always @* begin
        forwardA = 2'b00;
        forwardB = 2'b00;
        blockedA = 1'b0;
        blockedB = 1'b0;

        if (ex_match_a) begin
            if (EX_MEM_resultReady) forwardA = 2'b10;
            else                    blockedA = 1'b1;
        end else if (wb_match_a) begin
            forwardA = 2'b01;
        end

        if (ex_match_b) begin
            if (EX_MEM_resultReady) forwardB = 2'b10;
            else                    blockedB = 1'b1;
        end else if (wb_match_b) begin
            forwardB = 2'b01;
        end
    end
endmodule
