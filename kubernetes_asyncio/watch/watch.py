# Copyright 2016 The Kubernetes Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import pydoc
from functools import partial

from kubernetes_asyncio import client

PYDOC_RETURN_LABEL = ":return:"

# Removing this suffix from return type name should give us event's object
# type. e.g., if list_namespaces() returns "NamespaceList" type,
# then list_namespaces(watch=true) returns a stream of events with objects
# of type "Namespace". In case this assumption is not true, user should
# provide return_type to Watch class's __init__.
TYPE_LIST_SUFFIX = "List"


class SimpleNamespace:

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _find_return_type(func):
    for line in pydoc.getdoc(func).splitlines():
        if line.startswith(PYDOC_RETURN_LABEL):
            return line[len(PYDOC_RETURN_LABEL):].strip()
    return ""


async def iter_resp_lines(resp):
    line = await resp.content.readline()
    if isinstance(line, bytes):
        line = line.decode('utf8')
    return line


class Stream(object):

    def __init__(self, func, *args, **kwargs):
        pass


class Watch(object):

    def __init__(self, return_type=None):
        self._raw_return_type = return_type
        self._stop = False
        self._api_client = client.ApiClient()
        self.resource_version = 0

    def stop(self):
        self._stop = True

    def get_return_type(self, func):
        if self._raw_return_type:
            return self._raw_return_type
        return_type = _find_return_type(func)
        if return_type.endswith(TYPE_LIST_SUFFIX):
            return return_type[:-len(TYPE_LIST_SUFFIX)]
        return return_type

    def unmarshal_event(self, data, return_type):
        js = json.loads(data)
        js['raw_object'] = js['object']
        if return_type:
            obj = SimpleNamespace(data=json.dumps(js['raw_object']))
            js['object'] = self._api_client.deserialize(obj, return_type)
            if hasattr(js['object'], 'metadata'):
                self.resource_version = js['object'].metadata.resource_version
        return js

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.next()

    async def next(self):
        if self.resp is None:
            self.resp = await self.func()

        if self._stop:
            raise StopAsyncIteration

        ret = await iter_resp_lines(self.resp)
        ret = self.unmarshal_event(ret, self.return_type)
        return ret

    def stream(self, func, *args, **kwargs):
        """Watch an API resource and stream the result back via a generator.

        :param func: The API function pointer. Any parameter to the function
                     can be passed after this parameter.

        :return: Event object with these keys:
                   'type': The type of event such as "ADDED", "DELETED", etc.
                   'raw_object': a dict representing the watched object.
                   'object': A model representation of raw_object. The name of
                             model will be determined based on
                             the func's doc string. If it cannot be determined,
                             'object' value will be the same as 'raw_object'.

        Example:
            v1 = kubernetes_asyncio.client.CoreV1Api()
            watch = kubernetes_asyncio.watch.Watch()
            async for e in watch.stream(v1.list_namespace, timeout_seconds=10):
                type = e['type']
                object = e['object']  # object is one of type return_type
                raw_object = e['raw_object']  # raw_object is a dict
                ...
                if should_stop:
                    watch.stop()
        """
        self._stop = False
        self.return_type = self.get_return_type(func)
        kwargs['watch'] = True
        kwargs['_preload_content'] = False
        timeouts = ('timeout_seconds' in kwargs)

        self.func = partial(func, *args, **kwargs)
        self.resp = None

        return self
