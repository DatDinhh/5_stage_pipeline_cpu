addi r1, r0, -1
lui  r2, 0x8000
ori  r2, r2, 1
add  r3, r2, r1
addu r4, r3, r1
sub  r5, r0, r1
subu r6, r0, r2
and  r7, r2, r1
or   r8, r0, r2
xor  r9, r2, r1
nor  r10, r2, r0
slt  r11, r2, r1
sll  r12, r1, 0
sll  r13, r1, 31
srl  r14, r2, 31
sra  r15, r2, 31
andi r16, r1, 0x00ff
ori  r17, r0, 0xa5a5
xori r18, r17, 0xffff
addi r19, r0, -32768
done:
nop
