jal  target
addi r1, r0, 99
sw   r1, 0(r0)
target:
addi r2, r31, 1
j    done
addi r3, r0, 99
done:
nop
