from pynq import Bitstream
import time
PR_1_bit = Bitstream('/home/xilinx/bitstreams/PR_1_sub.bit', None, True)
PR_1_bit.download()
time.sleep(5)