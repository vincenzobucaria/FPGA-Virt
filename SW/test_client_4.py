from pynq import Bitstream
import time
time.sleep(2)
PR_2_bit = Bitstream('/home/xilinx/bitstreams/PR_0_sum.bit', None, True)
PR_2_bit.download()