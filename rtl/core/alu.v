`timescale 1ns/1ps
module alu (
    input  wire [31:0] in1,
    input  wire [31:0] in2,
    input  wire [4:0]  shamt,
    input  wire [2:0]  aluOp,
    input  wire [5:0]  funct,
    output reg         zero,
    output reg  [31:0] result
);
    wire [31:0] sra = $signed(in2) >>> shamt;
    always @* begin
        result = 32'b0;
        case (aluOp)
            3'b000: begin
                case (funct)
                    6'h20,6'h21: result = in1 + in2;
                    6'h22,6'h23: result = in1 - in2;
                    6'h24:       result = in1 & in2;
                    6'h25:       result = in1 | in2;
                    6'h26:       result = in1 ^ in2;
                    6'h27:       result = ~(in1 | in2);
                    6'h2a:       result = ($signed(in1) < $signed(in2)) ? 32'd1 : 32'd0;
                    6'h00:       result = in2 << shamt;
                    6'h02:       result = in2 >> shamt;
                    6'h03:       result = sra;
                    default:     result = 32'd0;
                endcase
            end
            3'b001: result = in1 - in2;
            3'b010: result = in1 + in2;
            3'b011: result = in1 & in2;
            3'b100: result = in1 | in2;
            3'b101: result = in1 ^ in2;
            3'b110: result = ($signed(in1) < $signed(in2)) ? 32'd1 : 32'd0;
            3'b111: result = {in2[15:0],16'b0};
            default: result = 32'b0;
        endcase
        zero = (result == 32'b0);
    end
endmodule
