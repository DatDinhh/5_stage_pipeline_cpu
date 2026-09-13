lui  r1, 0x0001
lw   r2, 0(r1)
addi r3, r0, 9
done:
nop
.org 0x80
addi r1, r0, 0
eret
