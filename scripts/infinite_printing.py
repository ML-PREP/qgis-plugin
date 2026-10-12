import asyncio
import time


def infinite_print():
    while True:
        print(f"Test script, this is using QProcess as a separate OS process")
        time.sleep(5)


if __name__ == "__main__":
    infinite_print()
