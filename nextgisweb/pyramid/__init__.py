# -*- coding: utf-8 -*-
import sys
import os.path
import re
import codecs
from hashlib import md5
from StringIO import StringIO
from pkg_resources import resource_filename, get_distribution
from collections import namedtuple

from pyramid.config import Configurator
from pyramid.authorization import ACLAuthorizationPolicy
from pyramid.events import BeforeRender

import pyramid_tm
import pyramid_mako

from ..package import pkginfo
from ..component import Component

from .util import (
    viewargs,
    ClientRoutePredicate,
    RequestMethodPredicate,
    JsonPredicate)
from .auth import AuthenticationPolicy

__all__ = ['viewargs', ]


class RouteHelper(object):

    def __init__(self, name, config):
        self.config = config
        self.name = name

    def add_view(self, view=None, **kwargs):
        if 'route_name' not in kwargs:
            kwargs['route_name'] = self.name

        self.config.add_view(view=view, **kwargs)
        return self


class ExtendedConfigurator(Configurator):

    def add_route(self, name, pattern=None, **kwargs):
        """ Расширенное добавление маршрута

        Синтаксический сахар, позволяющий записать часто встречающуюся
        конструкцию вида:

            config.add_route('foo', '/foo')
            config.add_view(foo_get, route_name='foo', request_method='GET')
            config.add_view(foo_post, route_name='foo', request_method='POST')


        Более компактным способом:

            config.add_route('foo', '/foo') \
                .add_view(foo_get, request_method='GET') \
                .add_view(foo_post, request_method='POST')

        """

        super(ExtendedConfigurator, self).add_route(
            name, pattern=pattern, **kwargs)

        return RouteHelper(name, self)

    def add_view(self, view=None, **kwargs):
        vargs = getattr(view, '__viewargs__', None)
        if vargs:
            kwargs = dict(vargs, **kwargs)

        super(ExtendedConfigurator, self).add_view(view=view, **kwargs)


DistInfo = namedtuple('DistInfo', ['name', 'version', 'commit'])


class PyramidComponent(Component):
    identity = 'pyramid'

    def make_app(self, settings=None):
        settings = dict(self._settings, **settings)

        settings['mako.directories'] = 'nextgisweb:templates/'

        # Если включен режим отладки, то добавляем mako-фильтр, который
        # проверяет была ли переведена строка перед выводом.

        if self.env.core.debug:
            settings['mako.default_filters'] = ['tcheck', 'h']
            settings['mako.imports'] = settings.get('mako.imports', []) \
                + ['from nextgisweb.i18n import tcheck', ]

        # Если в конфиге pyramid не указано иное, то используем ту локаль,
        # которая указана в компоненте core, хотя зачем такое нужно не ясно.

        plockey = 'pyramid.default_locale_name'
        if plockey not in settings and self.env.core.locale_default is not None:
            settings[plockey] = self.env.core.locale_default

        config = ExtendedConfigurator(settings=settings)

        # Заменяем стандартный localizer от pyramid на свой, родной очень
        # родной завязан на translationstring, который странно работает с
        # интерполяцией строк через оператор %.

        def localizer(request):
            return request.env.core.localizer(request.locale_name)
        config.add_request_method(localizer, 'localizer', property=True)

        # TODO: Нужно отказаться от translation dirs полностью!
        # Сейчас используются только для поиска jed-файлов.

        for pkg in pkginfo.packages:
            dirname = resource_filename(pkg, 'locale')
            if os.path.isdir(dirname):
                config.add_translation_dirs(dirname)

        config.add_route_predicate('client', ClientRoutePredicate)

        config.add_view_predicate('method', RequestMethodPredicate)
        config.add_view_predicate('json', JsonPredicate)

        # Возможность доступа к Env через request.env
        config.set_request_property(lambda (req): self._env, 'env')

        config.include(pyramid_tm)
        config.include(pyramid_mako)

        # Фильтр для быстрого перевода строк. Определяет функцию tr, которую
        # можно использовать вместо request.localizer.translate в шаблонах.
        def tr_subscriber(event):
            event['tr'] = event['request'].localizer.translate
        config.add_subscriber(tr_subscriber, BeforeRender)

        assert 'secret' in settings, 'Secret not set!'
        authn_policy = AuthenticationPolicy(settings=settings)
        config.set_authentication_policy(authn_policy)

        authz_policy = ACLAuthorizationPolicy()
        config.set_authorization_policy(authz_policy)

        if 'help_page' in settings:
            with codecs.open(settings['help_page'], 'rb', 'utf-8') as fp:
                self.help_page = fp.read()
        else:
            self.help_page = None

        # Чтобы не приходилось вручную чистить кеш статики, сделаем
        # так, чтобы у них всегда были разные URL. В качестве ключа
        # используем хеш md5 от всех установленных в окружении пакетов,
        # который можно вытянуть через pip freeze. Так же pip freeze
        # вместе с версиями возвращает текущий коммит, для пакетов из
        # VCS, что тоже полезно.

        # Наверное это можно как-то получше сделать, но делаем так:
        # перенаправляем sys.stdout в StringIO, запускаем pip freeze и
        # затем возвращаем sys.stdout на место.

        stdout = sys.stdout
        static_key = ''

        try:
            import pip
            buf = StringIO()
            sys.stdout = buf
            pip.main(['freeze', ])
            h = md5()
            h.update(buf.getvalue())
            static_key = '/' + h.hexdigest()
        finally:
            sys.stdout = stdout

        self.distinfo = []

        # Так же из вывода pip freeze читаем список установленных пакетов

        buf.seek(0)

        for l in buf:
            l = l.strip().lower()

            dinfo = None
            mpkg = re.match(r'(.+)==(.+)', l)
            if mpkg:
                dinfo = DistInfo(
                    name=mpkg.group(1),
                    version=mpkg.group(2),
                    commit=None)

            mgit = re.match(r'-e\sgit\+.+\@(.{8}).{32}\#egg=(\w+).*$', l)
            if mgit:
                dinfo = DistInfo(
                    name=mgit.group(2),
                    version=get_distribution(mgit.group(2)).version,
                    commit=mgit.group(1))

            if dinfo is not None:
                self.distinfo.append(dinfo)
            else:
                self.logger.warn("Could not parse pip freeze line: %s", l)

        config.add_static_view(
            '/static%s/asset' % static_key,
            'nextgisweb:static', cache_max_age=3600)

        config.add_route('amd_package', '/static%s/amd/*subpath' % static_key) \
            .add_view('nextgisweb.views.amd_package')

        for comp in self._env.chain('setup_pyramid'):
            comp.setup_pyramid(config)

        return config

    def setup_pyramid(self, config):
        from . import view, api
        view.setup_pyramid(self, config)
        api.setup_pyramid(self, config)

    settings_info = (
        dict(key='secret', desc=u"Ключ, используемый для шифрования cookies (обязательно)"),
        dict(key='help_page', desc=u"HTML-справка"),
        dict(key='logo', desc=u"Логотип системы"),
        dict(key='favicon', desc=u"Значок для избранного"),
        dict(key='home_url', desc=u"Ссылка для редиректа, при заходе на /")
    )
