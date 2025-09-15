#!/usr/bin/env python3
"""
Stop OpenPyLivox monitoring thread to prevent buffer errors
"""
import threading
import time

def stop_all_monitoring_threads():
    """Find and stop all OpenPyLivox monitoring threads"""
    stopped = 0
    for thread in threading.enumerate():
        if hasattr(thread, '_target') and thread._target:
            # Check if it's a monitoring thread
            target_name = getattr(thread._target, '__name__', '')
            if 'monitoring' in target_name.lower() or 'run' in target_name:
                # Check if it has the problematic socket
                if hasattr(thread, '_args') and thread._args:
                    obj = thread._args[0] if isinstance(thread._args, tuple) else thread._args
                    if hasattr(obj, 't_socket'):
                        try:
                            obj.t_socket.close()
                            print(f"Closed socket for thread: {thread.name}")
                            stopped += 1
                        except:
                            pass
                    if hasattr(obj, 'started'):
                        obj.started = False
                        print(f"Stopped thread: {thread.name}")
                        stopped += 1
    return stopped

if __name__ == "__main__":
    stopped = stop_all_monitoring_threads()
    print(f"Stopped {stopped} monitoring threads")