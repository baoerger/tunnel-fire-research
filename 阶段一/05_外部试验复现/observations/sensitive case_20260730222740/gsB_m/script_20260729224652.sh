#!/bin/bash

#SBATCH --job-name=fds
#SBATCH --partition=6240-36C-192G
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=36
#SBATCH -o %j.out
#SBATCH -e %j.err

module load FDS-6.9.1

mpirun -np 22 fds gsB_m.fds