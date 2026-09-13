`timescale 1ns/1ps
module hazard_unit (
    input  wire       ID_EX_valid,
    input  wire       ID_EX_memRead,
    input  wire [4:0] ID_EX_rt,
    input  wire       IF_ID_valid,
    input  wire [4:0] IF_ID_rs,
    input  wire [4:0] IF_ID_rt,
    input  wire       IF_ID_usesRs,
    input  wire       IF_ID_usesRt,
    output reg        stall,
    output reg        pcWrite,
    output reg        IF_ID_Write,
    output reg        controlBubble
);
    wire load_producer = ID_EX_valid && ID_EX_memRead && (ID_EX_rt != 5'd0);
    wire dependency_rs = IF_ID_valid && IF_ID_usesRs && (ID_EX_rt == IF_ID_rs);
    wire dependency_rt = IF_ID_valid && IF_ID_usesRt && (ID_EX_rt == IF_ID_rt);

    always @* begin
        stall         = load_producer && (dependency_rs || dependency_rt);
        pcWrite       = ~stall;
        IF_ID_Write   = ~stall;
        controlBubble = stall;
    end
endmodule
