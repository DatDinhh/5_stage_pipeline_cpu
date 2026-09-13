addi r10, r0, 64
lw   r1, 0(r10)
add  r2, r1, r1
addi r3, r2, 1
sb   r3, 1(r10)
lbu  r4, 1(r10)
addi r11, r0, 68
addi r11, r11, 4
sh   r2, 0(r11)
lhu  r5, 0(r11)
sw   r3, 8(r10)
lw   r6, 8(r10)
done:
nop
