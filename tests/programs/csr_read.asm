addi r1, r0, 1
addi r2, r0, 2
lui  r10, 0xffff
ori  r10, r10, 0xf000
lw   r3, 8(r10)
lw   r4, 0xf0(r10)
lw   r5, 0xf4(r10)
done:
nop
