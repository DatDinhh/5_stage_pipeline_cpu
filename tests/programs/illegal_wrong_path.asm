addi r1, r0, 1
.word 0xfc000000
addi r2, r0, 99
sw   r2, 0(r0)
done:
nop
.org 0x80
j done
nop
