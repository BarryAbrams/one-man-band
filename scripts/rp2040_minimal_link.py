from smbus2 import SMBus
import curses

addr = 0x12 # bus address
bus = SMBus(1) # indicates /dev/ic2-1

numb = 1

give_move = curses.initscr()
curses.noecho()
give_move.nodelay(1) # set getch() non-blocking

#print ("Enter 1 for ON or 0 for OFF")
num = 0
while 1:

	push_key = give_move.getch()#input(">>>>   ")

	if push_key == ord("1"):
		if(num <= 250):
			num += 5
			#bus.write_byte(addr, 0x1) # switch it on
			bus.write_byte(addr, num)
	
	elif push_key == ord("0"):
		if(num >= 5):
			num -= 5
			#bus.write_byte(addr, 0x0) # switch it on
			bus.write_byte(addr, num)