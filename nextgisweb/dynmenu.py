# -*- coding: utf-8 -*-


class DynMenu(object):

    def __init__(self, *args):
        self._items = list()

        for item in args:
            self.add(item)

    def add(self, item):
        self._items.append(item)

    def build(self, args):
        result = list()

        for item in self._items:
            if isinstance(item, DynItem):
                for subitem in item.build(args):
                    result.append(subitem)
            else:
                result.append(item)

        result.sort(key=lambda item: item.key)
        return result


class Item(object):

    def __init__(self, key):

        if isinstance(key, basestring):
            key = tuple(key.split('/'))
        elif key is None:
            key = ()

        self._key = key

    @property
    def key(self):
        return self._key

    @property
    def level(self):
        return len(self._key) - 1


class DynItem(Item):

    def __init__(self, key=None):
        super(DynItem, self).__init__(key)

    def build(self, args):
        pass


class Label(Item):

    def __init__(self, key, label):
        super(Label, self).__init__(key)
        self._label = label

    @property
    def label(self):
        return self._label


class Link(Item):

    def __init__(self, key, label, url):
        super(Link, self).__init__(key)
        self._label = label
        self._url = url

    @property
    def label(self):
        return self._label

    @property
    def url(self):
        return self._url