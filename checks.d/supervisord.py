import socket
import time
import xmlrpclib

from checks import AgentCheck

import supervisor.xmlrpc

DEFAULT_HOST = 'localhost'
DEFAULT_PORT = '9001'
DEFAULT_SOCKET_IP = 'http://127.0.0.1'

DD_STATUS = {
    'STOPPED': AgentCheck.CRITICAL,
    'STARTING': AgentCheck.OK,
    'RUNNING': AgentCheck.OK,
    'BACKOFF': AgentCheck.UNKNOWN,
    'STOPPING': AgentCheck.CRITICAL,
    'EXITED': AgentCheck.CRITICAL,
    'FATAL': AgentCheck.CRITICAL,
    'UNKNOWN': AgentCheck.UNKNOWN
}

PROCESS_STATUS = {
    AgentCheck.CRITICAL: 'down',
    AgentCheck.OK: 'up',
    AgentCheck.UNKNOWN: 'unknown'
}

SERVER_TAG = 'supervisord_server'

PROCESS_TAG = 'supervisord_process'

FORMAT_TIME = lambda x: time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(x))


class SupervisordCheck(AgentCheck):

    def check(self, instance):
        server_name = instance.get('name')

        if not server_name or not len(server_name):
            raise Exception("Supervisord server name not specified in yaml configuration.")

        server_check = instance.get('server_check', True)
        supervisor = self._connect(instance)
        count_by_status = {
            AgentCheck.OK: 0,
            AgentCheck.CRITICAL: 0,
            AgentCheck.UNKNOWN: 0
        }

        # Grab process information
        try:
            proc_names = instance.get('proc_names')
            if proc_names:
                if not isinstance(proc_names, list) or not len(proc_names):
                    raise Exception("Empty or invalid proc_names.")
                processes = []
                for proc_name in proc_names:
                    try:
                        processes.append(supervisor.getProcessInfo(proc_name))
                    except xmlrpclib.Fault, e:
                        if e.faultCode == 10: # bad process name
                            self.warning('Process not found: %s' % proc_name)
                        else:
                            raise Exception('An error occurred while reading'
                                            'process %s information: %s %s'
                                            % (proc_name, e.faultCode, e.faultString))
            else:
                processes = supervisor.getAllProcessInfo()
        except socket.error:
            host = instance.get('host', DEFAULT_HOST)
            port = instance.get('port', DEFAULT_PORT)
            sock = instance.get('socket')
            if server_check:  # Report connection failure
                message = 'Supervisord server %s is down.' % server_name
                self.service_check('supervisord.server.check', AgentCheck.CRITICAL,
                                   tags=['supervisord',
                                         '%s:%s' % (SERVER_TAG, server_name)],
                                   message=message)
            if sock is None:
                raise Exception('Cannot connect to http://%s:%s. '
                                'Make sure supervisor is running and XML-RPC '
                                'inet interface is enabled.' % (host, port))
            else:
                raise Exception('Cannot connect to %s. Make sure sure supervisor '
                                'is running and socket is enabled and socket file'
                                ' has the right permissions' % sock)
        except xmlrpclib.ProtocolError, e:
            if e.errcode == 401: # authorization error
                raise Exception('Username or password to %s are incorrect.' %
                                server_name)
            else:
                raise Exception('An error occurred while connecting to %s: '
                                '%s %s ' % (servere_name, e.errcode, e.errmsg))

        # Report service checks and uptime for each process
        for proc in processes:
            proc_name = proc['name']
            tags = ['supervisord',
                    '%s:%s' % (SERVER_TAG, server_name),
                    '%s:%s' % (PROCESS_TAG, proc_name)]

            # Report Service Check
            status = DD_STATUS[proc['statename']]
            msg = self._build_message(proc)
            count_by_status[status] += 1
            self.service_check('supervisord.process.check',
                               status, tags=tags, message=msg)
            # Report Uptime
            uptime = self._extract_uptime(proc)
            self.gauge('supervisord.process.uptime', uptime, tags=tags)

        # Report counts by status
        tags = ['supervisord', '%s:%s' % (SERVER_TAG, server_name)]
        for status in PROCESS_STATUS:
            self.gauge('supervisord.process.count', count_by_status[status],
                       tags=tags + ['status:%s' % PROCESS_STATUS[status]])

    @staticmethod
    def _connect(instance):
        sock = instance.get('socket')
        if sock is not None:
            host = instance.get('host', DEFAULT_SOCKET_IP)
            transport = supervisor.xmlrpc.SupervisorTransport(None, None, sock)
            server = xmlrpclib.ServerProxy(host, transport=transport)
        else:
            host = instance.get('host', DEFAULT_HOST)
            port = instance.get('port', DEFAULT_PORT)
            user = instance.get('user')
            password = instance.get('pass')
            auth = '%s:%s@' % (user, password) if user and password else ''
            server = xmlrpclib.Server('http://%s%s:%s/RPC2' % (auth, host, port))
        return server.supervisor

    @staticmethod
    def _extract_uptime(proc):
        start, now = int(proc['start']), int(proc['now'])
        status = proc['statename']
        active_state = status in ['BACKOFF', 'RUNNING', 'STOPPING']
        return now - start if active_state else 0

    @staticmethod
    def _build_message(proc):
        start, stop, now = int(proc['start']), int(proc['stop']), int(proc['now'])
        proc['now_str'] = FORMAT_TIME(now)
        proc['start_str'] = FORMAT_TIME(start)
        proc['stop_str'] = '' if stop == 0 else FORMAT_TIME(stop)

        return """Current time: %(now_str)s
Process name: %(name)s
Process group: %(group)s
Description: %(description)s
Error log file: %(stderr_logfile)s
Stdout log file: %(stdout_logfile)s
Log file: %(logfile)s
State: %(statename)s
Start time: %(start_str)s
Stop time: %(stop_str)s
Exit Status: %(exitstatus)s""" % proc
