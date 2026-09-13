addi r1, r0, 0
addi r2, r0, 3
loop:
addi r1, r1, 1
bne  r1, r2, loop
beq  r1, r2, taken
addi r20, r0, 99
sw   r20, 0(r0)
taken:
bne  r1, r2, wrong
addi r3, r0, 7
j    done
wrong:
addi r3, r0, 99
done:
nop
