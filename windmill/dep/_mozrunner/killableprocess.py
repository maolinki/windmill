# killableprocess - subprocesses which can be reliably killed
#
# Parts of this module are copied from the subprocess.py file contained
# in the Python distribution.
#
# Copyright (c) 2003-2004 by Peter Astrand <astrand@lysator.liu.se>
#
# Additions and modifications written by Benjamin Smedberg
# <benjamin@smedbergs.us> are Copyright (c) 2006 by the Mozilla Foundation
# <http://www.mozilla.org/>
#
# More Modifications
# Copyright (c) 2006-2007 by Mike Taylor <bear@code-bear.com>
# Copyright (c) 2007-2008 by Mikeal Rogers <mikeal@mozilla.com>
#
# By obtaining, using, and/or copying this software and/or its
# associated documentation, you agree that you have read, understood,
# and will comply with the following terms and conditions:
#
# Permission to use, copy, modify, and distribute this software and
# its associated documentation for any purpose and without fee is
# hereby granted, provided that the above copyright notice appears in
# all copies, and that both that copyright notice and this permission
# notice appear in supporting documentation, and that the name of the
# author not be used in advertising or publicity pertaining to
# distribution of the software without specific, written prior
# permission.
#
# THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS SOFTWARE,
# INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS.
# IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, INDIRECT OR
# CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
# OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT,
# NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION
# WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

"""killableprocess - Subprocesses which can be reliably killed

This module is a subclass of the builtin "subprocess" module. It allows
processes that launch subprocesses to be reliably killed on Windows (via the Popen.kill() method.

It also adds a timeout argument to Wait() for a limited period of time before
forcefully killing the process.

Note: On Windows, this module requires Windows 2000 or higher (no support for
Windows 95, 98, or NT 4.0). It also requires ctypes, which is bundled with
Python 2.5+ or available from http://python.net/crew/theller/ctypes/
"""

import subprocess
import sys
import os
import time
import datetime
import types
import exceptions

try:
    from subprocess import CalledProcessError
except ImportError:
    # Python 2.4 doesn't implement CalledProcessError
    class CalledProcessError(Exception):
        """This exception is raised when a process run by check_call() returns
        a non-zero exit status. The exit status will be stored in the
        returncode attribute."""
        def __init__(self, returncode, cmd):
            self.returncode = returncode
            self.cmd = cmd
        def __str__(self):
            return "Command '%s' returned non-zero exit status %d" % (self.cmd, self.returncode)

mswindows = (sys.platform == "win32")

if mswindows:
    import winprocess
else:
    import signal

def call(*args, **kwargs):
    waitargs = {}
    if "timeout" in kwargs:
        waitargs["timeout"] = kwargs.pop("timeout")

    return Popen(*args, **kwargs).wait(**waitargs)

def check_call(*args, **kwargs):
    """Call a program with an optional timeout. If the program has a non-zero
    exit status, raises a CalledProcessError."""

    retcode = call(*args, **kwargs)
    if retcode:
        cmd = kwargs.get("args")
        if cmd is None:
            cmd = args[0]
        raise CalledProcessError(retcode, cmd)

if not mswindows:
    def DoNothing(*args):
        pass

class Popen(subprocess.Popen):
    if mswindows:
        def _execute_child(self, args, executable, preexec_fn, close_fds,
                           cwd, env, universal_newlines, startupinfo,
                           creationflags, shell,
                           p2cread, p2cwrite,
                           c2pread, c2pwrite,
                           errread, errwrite):
            if not isinstance(args, types.StringTypes):
                args = subprocess.list2cmdline(args)

            if startupinfo is None:
                startupinfo = winprocess.STARTUPINFO()

            if None not in (p2cread, c2pwrite, errwrite):
                startupinfo.dwFlags |= winprocess.STARTF_USESTDHANDLES
                
                startupinfo.hStdInput = int(p2cread)
                startupinfo.hStdOutput = int(c2pwrite)
                startupinfo.hStdError = int(errwrite)
            if shell:
                startupinfo.dwFlags |= winprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = winprocess.SW_HIDE
                comspec = os.environ.get("COMSPEC", "cmd.exe")
                args = comspec + " /c " + args

            # We create a new job for this process, so that we can kill
            # the process and any sub-processes 
            self._job = winprocess.CreateJobObject()

            creationflags |= winprocess.CREATE_SUSPENDED
            creationflags |= winprocess.CREATE_UNICODE_ENVIRONMENT

            hp, ht, pid, tid = winprocess.CreateProcess(
                executable, args,
                None, None, # No special security
                1, # Must inherit handles!
                creationflags,
                winprocess.EnvironmentBlock(env),
                cwd, startupinfo)
            
            self._child_created = True
            self._handle = hp
            self._thread = ht
            self.pid = pid
            self.tid = tid

            winprocess.AssignProcessToJobObject(self._job, hp)
            winprocess.ResumeThread(ht)

            if p2cread is not None:
                p2cread.Close()
            if c2pwrite is not None:
                c2pwrite.Close()
            if errwrite is not None:
                errwrite.Close()
            time.sleep(.1)

    def kill(self, group=True):
        """Kill the process. If group=True, all sub-processes will also be killed."""
        if mswindows:
            if group:
                winprocess.TerminateJobObject(self._job, 127)
            else:
                winprocess.TerminateProcess(self._handle, 127)
            self.returncode = 127    
        else:
            if group:
                try:
                    os.killpg(self.pid, signal.SIGKILL)
                except: pass
            else:
                os.kill(self.pid, signal.SIGKILL)
            self.returncode = -9

    def wait(self, timeout=None, group=True):
        """Wait for the process to terminate. Returns returncode attribute.
        If timeout seconds are reached and the process has not terminated,
        it will be forcefully killed. If timeout is -1, wait will not
        time out."""
        
        if timeout is not None:
            timeout = timeout * 1000

        if self.returncode is not None:
            return self.returncode

        starttime = datetime.datetime.now()

        if mswindows:
            if timeout is None:
                timeout = -1
            rc = winprocess.WaitForSingleObject(self._handle, timeout)
            
            if rc != winprocess.WAIT_TIMEOUT: 
                while (starttime - datetime.datetime.now()).microseconds < timeout or ( winprocess.QueryInformationJobObject(self._job, 8)['BasicInfo']['ActiveProcesses'] > 0 ):
                    time.sleep(.5)            
            
            if (starttime - datetime.datetime.now()).microseconds > timeout:
                self.kill(group)
            else:
                self.returncode = winprocess.GetExitCodeProcess(self._handle)
        else:
            if sys.platform == 'linux2' or sys.platform == 'cygwin':
                def group_wait():
                    os.waitpid(self.pid, 0)
                    return self.returncode
            elif sys.platform == 'darwin':
                def group_wait():
                    try:
                        while 1:
                            os.killpg(self.pid, signal.SIG_DFL)
                            time.sleep(.5)
                    except exceptions.OSError:
                        return self.returncode
                        
            if timeout is None:
                if group is True:
                    return group_wait()
                else:
                    subprocess.Popen.wait(self)
                    return self.returncode

            returncode = False

            while (starttime - datetime.datetime.now()).microseconds < timeout or ( returncode is False ):
                if group is True:
                    return group_wait()
                else:
                    if subprocess.poll() is not None:
                        returncode = self.returncode
                time.sleep(.5)
            return self.returncode        
                
        return self.returncode
    # We get random maxint errors from subprocesses __del__
    __del__ = lambda self: None        
        
def setpgid_preexec_fn():
    os.setpgid(0, 0)
        
def runCommand(cmd, **kwargs):
    if sys.platform != "win32":
        return Popen(cmd, preexec_fn=setpgid_preexec_fn, **kwargs)
    else:
        return Popen(cmd, **kwargs)
