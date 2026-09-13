addi r1, r0, 1
lw   r2, 0(r1)
addi r3, r0, 7
done:
nop
.org 0x80
addi r1, r1, -1
eret
