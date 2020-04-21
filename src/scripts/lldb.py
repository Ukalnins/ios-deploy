import time
import os
import sys
import shlex
import lldb
from threading import Thread

class OutputListener(Thread):
    def __init__(self, debugger, process, out_path, err_path):
        Thread.__init__(self)

        self.daemon = True

        self.debugger = debugger
        self.process = process
        self.done = False
        self.listener = lldb.SBListener('iosdeploy_output_listener')
        self.out = open(out_path,'w') if out_path and len(out_path) > 0 else sys.stdout
        self.err = open(err_path,'w') if err_path and len(err_path) > 0 else sys.stdout

        self.debugger.GetListener().StopListeningForEvents(process.GetBroadcaster(),
                                                    lldb.SBProcess.eBroadcastBitSTDOUT
                                                  | lldb.SBProcess.eBroadcastBitSTDERR)
        self.listener.StartListeningForEvents(process.GetBroadcaster(),
                                                    lldb.SBProcess.eBroadcastBitSTDOUT
                                                  | lldb.SBProcess.eBroadcastBitSTDERR)

    def stop(self):
        self.done = True
        self.join()

    def run(self):

        def ProcessSTDOUT():
            stdout = self.process.GetSTDOUT(1024)
            while stdout:
                self.out.write(stdout)
                stdout = self.process.GetSTDOUT(1024)

        def ProcessSTDERR():
            stderr = self.process.GetSTDERR(1024)
            while stderr:
                self.err.write(stderr)
                stderr = self.process.GetSTDERR(1024)

        def CloseOut():
            if self.out != sys.stdout:
                self.out.close()
            if self.err != sys.stdout:
                self.err.close()
            sys.stdout.flush()

        event = lldb.SBEvent()
        while not self.done:
            if self.listener.WaitForEvent(1, event) and lldb.SBProcess.EventIsProcessEvent(event):

                type = event.GetType()

                if type & lldb.SBProcess.eBroadcastBitSTDOUT:
                    ProcessSTDOUT()

                if type & lldb.SBProcess.eBroadcastBitSTDERR:
                    ProcessSTDERR()

        ProcessSTDOUT()
        ProcessSTDERR()
        CloseOut()


output_thread = None
listener = None
startup_error = lldb.SBError()


def connect_command(debugger, command, result, internal_dict):
    # These two are passed in by the script which loads us
    connect_url = internal_dict['fruitstrap_connect_url']
    error = lldb.SBError()

    # We create a new listener here and will use it for both target and the process.
    # It allows us to prevent data races when both our code and internal lldb code
    # try to process STDOUT/STDERR messages
    global listener
    listener = lldb.SBListener('iosdeploy_state_listener')

    process = lldb.target.ConnectRemote(listener, connect_url, 'gdb-remote', error)
    listener.StartListeningForEventClass(debugger,
                                            lldb.SBTarget.GetBroadcasterClassName(),
                                            lldb.SBProcess.eBroadcastBitStateChanged)
    global output_thread
    output_thread = OutputListener(debugger, process, internal_dict['fruitstrap_output_path'], internal_dict['fruitstrap_error_path'])
    output_thread.start()


def cleanup_command(debugger, command, result, internal_dict):
    if output_thread and output_thread.is_alive():
        output_thread.stop()


def run_command(debugger, command, result, internal_dict):
    device_app = internal_dict['fruitstrap_device_app']
    args = command.split('--',1)
    lldb.target.modules[0].SetPlatformFileSpec(lldb.SBFileSpec(device_app))
    args_arr = []
    if len(args) > 1:
        args_arr = shlex.split(args[1])
    args_arr = args_arr + shlex.split('{args}')
    launchInfo = lldb.SBLaunchInfo(args_arr)
    global listener
    launchInfo.SetListener(listener)

    #This env variable makes NSLog, CFLog and os_log messages get mirrored to stderr
    #https://stackoverflow.com/a/39581193
    launchInfo.SetEnvironmentEntries(['OS_ACTIVITY_DT_MODE=enable'], True)

    envs_arr = []
    if len(args) > 1:
        envs_arr = shlex.split(args[1])
    envs_arr = envs_arr + shlex.split('{envs}')
    launchInfo.SetEnvironmentEntries(envs_arr, True)

    lldb.target.Launch(launchInfo, startup_error)
    lockedstr = ': Locked'
    if lockedstr in str(startup_error):
       print('\\nDevice Locked\\n')
       output_thread.stop()
       os._exit(254)
    else:
       print(str(startup_error))

def safequit_command(debugger, command, result, internal_dict):
    process = lldb.target.process
    state = process.GetState()
    if state == lldb.eStateRunning:
        output_thread.stop()
        process.Detach()
        os._exit(0)
    elif state > lldb.eStateRunning:
        output_thread.stop()
        os._exit(state)
    else:
        output_thread.stop()
        print('\\nApplication has not been launched\\n')
        os._exit(1)

def autoexit_command(debugger, command, result, internal_dict):

    process = lldb.target.process
    if not startup_error.Success():
        print('\\nPROCESS_NOT_STARTED\\n')
        os._exit({exitcode_app_crash})

    detectDeadlockTimeout = {detect_deadlock_timeout}
    printBacktraceTime = time.time() + detectDeadlockTimeout if detectDeadlockTimeout > 0 else None

    event = lldb.SBEvent()
    global listener
    debugger.GetListener().StopListeningForEvents(process.GetBroadcaster(),
                                               lldb.SBProcess.eBroadcastBitStateChanged)
    listener.StartListeningForEvents(process.GetBroadcaster(),
                                               lldb.SBProcess.eBroadcastBitStateChanged)

    while True:
        if listener.WaitForEvent(1, event) and lldb.SBProcess.EventIsProcessEvent(event):
            state = lldb.SBProcess.GetStateFromEvent(event)
            type = event.GetType()
        else:
            state = process.GetState()

        if state == lldb.eStateExited:
            output_thread.stop()
            sys.stdout.write( '\\nPROCESS_EXITED\\n' )
            os._exit(process.GetExitStatus())
        elif printBacktraceTime is None and state == lldb.eStateStopped:
            output_thread.stop()
            sys.stdout.write( '\\nPROCESS_STOPPED\\n' )
            debugger.HandleCommand('bt')
            os._exit({exitcode_app_crash})
        elif state == lldb.eStateCrashed:
            output_thread.stop()
            sys.stdout.write( '\\nPROCESS_CRASHED\\n' )
            debugger.HandleCommand('bt')
            os._exit({exitcode_app_crash})
        elif state == lldb.eStateDetached:
            output_thread.stop()
            sys.stdout.write( '\\nPROCESS_DETACHED\\n' )
            os._exit({exitcode_app_crash})
        elif printBacktraceTime is not None and time.time() >= printBacktraceTime:
            printBacktraceTime = None
            sys.stdout.write( '\\nPRINT_BACKTRACE_TIMEOUT\\n' )
            debugger.HandleCommand('process interrupt')
            debugger.HandleCommand('bt all')
            debugger.HandleCommand('continue')
            printBacktraceTime = time.time() + 5
