# Original nine-word ROM compatibility program.
addi r1, r0, 5
addi r2, r0, 10
add  r3, r1, r2
sw   r3, 0(r0)
retry:
lw   r4, 0(r0)
addi r4, r4, 1
sw   r4, 4(r0)
lw   r5, 4(r0)
done:
beq  r5, r0, retry
