addi r1, r0, 3
addi r2, r0, 0x55
sh   r2, 0(r1)
lhu  r3, 0(r1)
done:
nop
.org 0x80
addi r1, r1, 1
eret
