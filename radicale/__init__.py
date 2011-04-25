# -*- coding: utf-8 -*-
#
# This file is part of Radicale Server - Calendar Server
# Copyright © 2008-2011 Guillaume Ayoub
# Copyright © 2008 Nicolas Kandel
# Copyright © 2008 Pascal Halter
#
# This library is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Radicale.  If not, see <http://www.gnu.org/licenses/>.

"""
Radicale Server module.

This module offers 3 useful classes:

- ``HTTPServer`` is a simple HTTP server;
- ``HTTPSServer`` is a HTTPS server, wrapping the HTTP server in a socket
  managing SSL connections;
- ``CalendarHTTPHandler`` is a CalDAV request handler for HTTP(S) servers.

To use this module, you should take a look at the file ``radicale.py`` that
should have been included in this package.

"""

import os
import posixpath
import base64
import socket
# Manage Python2/3 different modules
# pylint: disable=F0401
try:
    from http import client, server
except ImportError:
    import httplib as client
    import BaseHTTPServer as server
# pylint: enable=F0401

from radicale import acl, config, ical, log, xmlutils


VERSION = "git"

# Decorators can access ``request`` protected functions
# pylint: disable=W0212

def _check(request, function):
    """Check if user has sufficient rights for performing ``request``."""
    # If we have no calendar or no acl, don't check rights
    if not request._calendar or not request.server.acl:
        return function(request)

    if request._calendar.owner is None and PERSONAL:
        # No owner and personal calendars, don't check rights
        return function(request)

    log.LOGGER.info(
        "Checking rights for calendar owned by %s" % request._calendar.owner)

    authorization = request.headers.get("Authorization", None)
    if authorization:
        challenge = authorization.lstrip("Basic").strip().encode("ascii")
        user, password = request._decode(base64.b64decode(challenge)).split(":")
    else:
        user = password = None

    if request.server.acl.has_right(request._calendar.owner, user, password):
        log.LOGGER.info("%s allowed" % request._calendar.owner)
        function(request)
    else:
        log.LOGGER.info("%s refused" % request._calendar.owner)
        request.send_response(client.UNAUTHORIZED)
        request.send_header(
            "WWW-Authenticate",
            "Basic realm=\"Radicale Server - Password Required\"")
        request.end_headers()

def _log_request_content(request, function):
    """Log the content of the request and store it in the request object."""
    log.LOGGER.info(
        "%s request at %s recieved from %s" % (
            request.command, request.path, request.client_address[0]))

    content_length = int(request.headers.get("Content-Length", 0))
    if content_length:
        request._content = request.rfile.read(content_length)
        log.LOGGER.debug(
            "Request headers:\n%s" % "\n".join(
                ": ".join((key, value))
                for key, value in request.headers.items()))
        log.LOGGER.debug("Request content:\n%s" % request._content)
    else:
        request._content = None

    function(request)

    if getattr(request, "_answer"):
        log.LOGGER.debug(
            "Response content:\n%s" % request._answer)

# pylint: enable=W0212


class HTTPServer(server.HTTPServer):
    """HTTP server."""
    PROTOCOL = "http"

    # Maybe a Pylint bug, ``__init__`` calls ``server.HTTPServer.__init__``
    # pylint: disable=W0231
    def __init__(self, address, handler, bind_and_activate=True):
        """Create server."""
        ipv6 = ":" in address[0]

        if ipv6:
            self.address_family = socket.AF_INET6

        # Do not bind and activate, as we might change socketopts
        server.HTTPServer.__init__(self, address, handler, False)

        if ipv6:
            # Only allow IPv6 connections to the IPv6 socket
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)

        if bind_and_activate:
            self.server_bind()
            self.server_activate()

        self.acl = acl.load()
    # pylint: enable=W0231


class HTTPSServer(HTTPServer):
    """HTTPS server."""
    PROTOCOL = "https"

    def __init__(self, address, handler, bind_and_activate=True):
        """Create server by wrapping HTTP socket in an SSL socket."""
        # Fails with Python 2.5, import if needed
        # pylint: disable=F0401
        import ssl
        # pylint: enable=F0401

        HTTPServer.__init__(self, address, handler, False)
        self.socket = ssl.wrap_socket(
            self.socket,
            server_side=True,
            certfile=config.get("server", "certificate"),
            keyfile=config.get("server", "key"),
            ssl_version=ssl.PROTOCOL_SSLv23)

        if bind_and_activate:
            self.server_bind()
            self.server_activate()


class CalendarHTTPHandler(server.BaseHTTPRequestHandler):
    """HTTP requests handler for calendars."""
    _encoding = config.get("encoding", "request")

    # Request handlers decorators
    check_rights = lambda function: lambda request: _check(request, function)
    log_request_content = \
        lambda function: lambda request: _log_request_content(request, function)

    # Maybe a Pylint bug, ``__init__`` calls ``server.HTTPServer.__init__``
    # pylint: disable=W0231
    def __init__(self, request, client_address, http_server):
        self._content = None
        self._answer = None
        server.BaseHTTPRequestHandler.__init__(
            self, request, client_address, http_server)
    # pylint: enable=W0231

    @property
    def _calendar(self):
        """The ``ical.Calendar`` object corresponding to the given path."""
        # ``self.path`` must be something like a posix path
        # ``normpath`` should clean malformed and malicious request paths
        attributes = posixpath.normpath(self.path.strip("/")).split("/")
        if attributes:
            if attributes[-1].endswith('.ics'):
                attributes.pop()
            path = "/".join(attributes[:min(len(attributes), 2)])
            return ical.Calendar(path)

    def _decode(self, text):
        """Try to decode text according to various parameters."""
        # List of charsets to try
        charsets = []

        # First append content charset given in the request
        content_type = self.headers.get("Content-Type", None)
        if content_type and "charset=" in content_type:
            charsets.append(content_type.split("charset=")[1].strip())
        # Then append default Radicale charset
        charsets.append(self._encoding)
        # Then append various fallbacks
        charsets.append("utf-8")
        charsets.append("iso8859-1")

        # Try to decode
        for charset in charsets:
            try:
                return text.decode(charset)
            except UnicodeDecodeError:
                pass
        raise UnicodeDecodeError

    def log_message(self, *args, **kwargs):
        """Disable inner logging management."""

    # Naming methods ``do_*`` is OK here
    # pylint: disable=C0103

    @log_request_content
    def do_GET(self):
        """Manage GET request."""
        self.do_HEAD()
        if self._answer:
            self.wfile.write(self._answer)

    @log_request_content
    @check_rights
    def do_HEAD(self):
        """Manage HEAD request."""
        item_name = xmlutils.name_from_path(self.path, self._calendar)
        if item_name:
            # Get calendar item
            item = self._calendar.get_item(item_name)
            if item:
                items = self._calendar.timezones
                items.append(item)
                answer_text = ical.serialize(
                    headers=self._calendar.headers, items=items)
                etag = item.etag
            else:
                self._answer = None
                self.send_response(client.GONE)
                return
        else:
            # Get whole calendar
            answer_text = self._calendar.text
            etag = self._calendar.etag

        self._answer = answer_text.encode(self._encoding)
        self.send_response(client.OK)
        self.send_header("Content-Length", len(self._answer))
        self.send_header("Content-Type", "text/calendar")
        self.send_header("Last-Modified", self._calendar.last_modified)
        self.send_header("ETag", etag)
        self.end_headers()

    @log_request_content
    @check_rights
    def do_DELETE(self):
        """Manage DELETE request."""
        item = self._calendar.get_item(
            xmlutils.name_from_path(self.path, self._calendar))
        if item and self.headers.get("If-Match", item.etag) == item.etag:
            # No ETag precondition or precondition verified, delete item
            self._answer = xmlutils.delete(self.path, self._calendar)

            self.send_response(client.NO_CONTENT)
            self.send_header("Content-Length", len(self._answer))
            self.end_headers()
            self.wfile.write(self._answer)
        else:
            # No item or ETag precondition not verified, do not delete item
            self.send_response(client.PRECONDITION_FAILED)

    @log_request_content
    @check_rights
    def do_MKCALENDAR(self):
        """Manage MKCALENDAR request."""
        self.send_response(client.CREATED)
        self.end_headers()

    @log_request_content
    def do_OPTIONS(self):
        """Manage OPTIONS request."""
        self.send_response(client.OK)
        self.send_header(
            "Allow", "DELETE, HEAD, GET, MKCALENDAR, "
            "OPTIONS, PROPFIND, PUT, REPORT")
        self.send_header("DAV", "1, calendar-access")
        self.end_headers()

    @log_request_content
    def do_PROPFIND(self):
        """Manage PROPFIND request."""
        self._answer = xmlutils.propfind(
            self.path, self._content, self._calendar,
            self.headers.get("depth", "infinity"))

        self.send_response(client.MULTI_STATUS)
        self.send_header("DAV", "1, calendar-access")
        self.send_header("Content-Length", len(self._answer))
        self.send_header("Content-Type", "text/xml")
        self.end_headers()
        self.wfile.write(self._answer)

    @log_request_content
    @check_rights
    def do_PUT(self):
        """Manage PUT request."""
        item_name = xmlutils.name_from_path(self.path, self._calendar)
        item = self._calendar.get_item(item_name)
        if (not item and not self.headers.get("If-Match")) or \
                (item and self.headers.get("If-Match", item.etag) == item.etag):
            # PUT allowed in 3 cases
            # Case 1: No item and no ETag precondition: Add new item
            # Case 2: Item and ETag precondition verified: Modify item
            # Case 3: Item and no Etag precondition: Force modifying item
            ical_request = self._decode(self._content)
            xmlutils.put(self.path, ical_request, self._calendar)
            etag = self._calendar.get_item(item_name).etag

            self.send_response(client.CREATED)
            self.send_header("ETag", etag)
            self.end_headers()
        else:
            # PUT rejected in all other cases
            self.send_response(client.PRECONDITION_FAILED)

    @log_request_content
    @check_rights
    def do_REPORT(self):
        """Manage REPORT request."""
        self._answer = xmlutils.report(self.path, self._content, self._calendar)

        self.send_response(client.MULTI_STATUS)
        self.send_header("Content-Length", len(self._answer))
        self.end_headers()
        self.wfile.write(self._answer)

    # pylint: enable=C0103
