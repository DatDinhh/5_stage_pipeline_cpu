lui  r10, 0xffff
ori  r10, r10, 0xf000
addi r1, r0, 42
sw   r1, 0(r10)
addi r2, r0, 99
done:
nop
.org 0x80
j done
nop
