`timescale 1ns/1ps
module control_unit (
    input  wire [5:0] opcode,
    input  wire [5:0] funct,
    output reg        regDst,
    output reg        aluSrc,
    output reg        memToReg,
    output reg        regWrite,
    output reg        memRead,
    output reg        memWrite,
    output reg        branch,
    output reg        branch_ne,
    output reg [2:0]  aluOp,
    output reg        jump,
    output reg        jal,
    output reg        immZeroExt,
    output reg [1:0]  memSize,
    output reg        loadSigned,
    output reg        eret,
    output reg        usesRs,
    output reg        usesRt,
    output reg        illegal
);
    localparam R_TYPE = 6'b000000;
    localparam LW     = 6'b100011;
    localparam SW     = 6'b101011;
    localparam BEQ    = 6'b000100;
    localparam BNE    = 6'b000101;
    localparam JUMP   = 6'b000010;
    localparam JALOP  = 6'b000011;
    localparam ADDI   = 6'b001000;
    localparam ANDI   = 6'b001100;
    localparam ORI    = 6'b001101;
    localparam XORI   = 6'b001110;
    localparam LUI    = 6'b001111;
    localparam LB     = 6'b100000;
    localparam LBU    = 6'b100100;
    localparam LH     = 6'b100001;
    localparam LHU    = 6'b100101;
    localparam SB     = 6'b101000;
    localparam SH     = 6'b101001;
    localparam ERET   = 6'b011000;

    always @* begin
        regDst=0; aluSrc=0; memToReg=0; regWrite=0; memRead=0; memWrite=0;
        branch=0; branch_ne=0; jump=0; jal=0; immZeroExt=0; aluOp=3'b000;
        memSize=2'b00; loadSigned=1'b1; eret=0; usesRs=0; usesRt=0; illegal=0;
        case (opcode)
            R_TYPE: begin
                case (funct)
                    6'h00, 6'h02, 6'h03: begin
                        regDst=1; regWrite=1; usesRt=1; aluOp=3'b000;
                    end
                    6'h20, 6'h21, 6'h22, 6'h23,
                    6'h24, 6'h25, 6'h26, 6'h27,
                    6'h2A: begin
                        regDst=1; regWrite=1; usesRs=1; usesRt=1; aluOp=3'b000;
                    end
                    default: illegal=1;
                endcase
            end
            LW   : begin aluSrc=1; memToReg=1; regWrite=1; memRead=1; usesRs=1; aluOp=3'b010; memSize=2'b00; loadSigned=1; end
            SW   : begin aluSrc=1; memWrite=1; usesRs=1; usesRt=1; aluOp=3'b010; memSize=2'b00; end
            BEQ  : begin branch=1; branch_ne=0; usesRs=1; usesRt=1; aluOp=3'b001; end
            BNE  : begin branch=1; branch_ne=1; usesRs=1; usesRt=1; aluOp=3'b001; end
            JUMP : begin jump=1; end
            JALOP: begin jump=1; jal=1; regWrite=1; end
            ADDI : begin aluSrc=1; regWrite=1; usesRs=1; aluOp=3'b010; end
            ANDI : begin aluSrc=1; regWrite=1; usesRs=1; immZeroExt=1; aluOp=3'b011; end
            ORI  : begin aluSrc=1; regWrite=1; usesRs=1; immZeroExt=1; aluOp=3'b100; end
            XORI : begin aluSrc=1; regWrite=1; usesRs=1; immZeroExt=1; aluOp=3'b101; end
            LUI  : begin aluSrc=1; regWrite=1; immZeroExt=1; aluOp=3'b111; end
            LB   : begin aluSrc=1; memToReg=1; regWrite=1; memRead=1; usesRs=1; aluOp=3'b010; memSize=2'b10; loadSigned=1; end
            LBU  : begin aluSrc=1; memToReg=1; regWrite=1; memRead=1; usesRs=1; aluOp=3'b010; memSize=2'b10; loadSigned=0; end
            LH   : begin aluSrc=1; memToReg=1; regWrite=1; memRead=1; usesRs=1; aluOp=3'b010; memSize=2'b01; loadSigned=1; end
            LHU  : begin aluSrc=1; memToReg=1; regWrite=1; memRead=1; usesRs=1; aluOp=3'b010; memSize=2'b01; loadSigned=0; end
            SB   : begin aluSrc=1; memWrite=1; usesRs=1; usesRt=1; aluOp=3'b010; memSize=2'b10; end
            SH   : begin aluSrc=1; memWrite=1; usesRs=1; usesRt=1; aluOp=3'b010; memSize=2'b01; end
            ERET : begin eret=1; end
            default: illegal=1;
        endcase
    end
endmodule
