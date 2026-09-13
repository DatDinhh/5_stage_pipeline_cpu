# Real memory traffic and wrong-path stores under the adversarial bus profile.
addi r10, r0, 0x100
addi r11, r0, 0x5a
sw   r11, 0(r10)
lw   r12, 0(r10)
addi r1, r0, 0
addi r2, r0, 3
loop:
addi r1, r1, 1
sw   r1, 4(r10)
bne  r1, r2, loop
beq  r12, r11, taken
sw   r11, 8(r10)
taken:
bne  r1, r2, wrong
lw   r3, 4(r10)
j    done
wrong:
sw   r11, 12(r10)
done:
nop
