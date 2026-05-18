
from smbus2 import SMBus
import sys

addr = 0x12
bus = SMBus(1)


def main() -> int:
   print("python is running")
   while True:
      bus.write_byte(addr, 0x41)
   return 0

if __name__ == "__main__":
    main()
