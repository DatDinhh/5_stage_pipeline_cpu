addi r10, r0, 64
lb   r1, 0(r10)
lb   r2, 1(r10)
lb   r3, 2(r10)
lbu  r4, 2(r10)
lb   r5, 3(r10)
lbu  r6, 3(r10)
lh   r7, 0(r10)
lh   r8, 2(r10)
lhu  r9, 2(r10)
addi r11, r0, 0xaa
sb   r11, 1(r10)
lui  r12, 0x1234
ori  r12, r12, 0x5678
sh   r12, 2(r10)
lw   r13, 0(r10)
sw   r13, 4(r10)
lw   r0, 4(r10)
done:
nop
