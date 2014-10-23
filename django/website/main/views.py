from __future__ import unicode_literals

from datetime import datetime
from os import path
import re

from braces.views import JSONResponseMixin
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import MultipleObjectsReturned
from django.core.urlresolvers import reverse
from django.http import HttpResponseRedirect, HttpResponse, Http404
from django.views.generic import TemplateView, RedirectView, View

from github import Github, UnknownObjectException
from markdown import markdown

from .models import LatestVersion, CachedStandard
from .standardscache import StandardsRepo, HTMLProducer


def get_repo():
    g = Github(client_id=settings.GITHUB_API_CLIENT_ID,
               client_secret=settings.GITHUB_API_CLIENT_SECRET)
    repo = g.get_repo(settings.STANDARD_GITHUB_REPO)
    return repo


def get_ordered_tags(repo):
    tags = repo.get_tags()
    list_tags = list(tags)
    date_format = '%a, %d %b %Y %H:%M:%S %Z'
    sorted_tags = \
        sorted(
            list_tags,
            key=lambda t: datetime.strptime(t.commit.stats.last_modified,
                                            date_format)
        )
    reversed_sorted = sorted_tags[::-1]
    latest_version = LatestVersion.objects.get()
    latest_version.tag_name = reversed_sorted[0].name
    latest_version.save()
    return reversed_sorted


def get_document_from_github(repo, path='README.md',
                             release='master', doctype='html'):
    """
    Get the standard markdown file from Github repo and decode it.
    Requires a repo and a valid release string. Does not check whether release
    string is valid to reduce calls to API.
    """
    try:
        contents = repo.get_contents(path, ref=release)
        document = contents.decoded_content
    except UnknownObjectException:
        if doctype == 'json':
            document = '{}'
        else:
            document = '<br />'
    return document


def render_markdown(document):
    return markdown(document, extensions=['footnotes', 'sane_lists', 'toc'])


def get_from_file(mdfile):
    with open(mdfile, 'r') as f:
        document = f.read()
    return document


def get_document_from_cache(repo, path, release, doctype='html'):
    document = ''
    try:
        cached = CachedStandard.objects.get(tag_name=release)
        if path == 'standard/standard.md':
            if cached.standard is '':
                raise CachedStandard.DoesNotExist
            else:
                document = cached.standard
        if path == 'standard/vocabulary.md':
            if cached.vocabulary == '':
                raise CachedStandard.DoesNotExist
            else:
                document = cached.vocabulary
        if path == 'standard/merging.md':
            if cached.merging == '':
                raise CachedStandard.DoesNotExist
            else:
                document = cached.merging
        if path == 'standard/worked_example.md':
            if cached.worked_example == '':
                raise CachedStandard.DoesNotExist
            else:
                document = cached.worked_example
        if path == 'standard/schema/release-schema.json':
            if cached.release_schema == '':
                raise CachedStandard.DoesNotExist
            else:
                document = cached.release_schema
        if path == 'standard/schema/release-package-schema.json':
            if cached.release_package_schema == '':
                raise CachedStandard.DoesNotExist
            else:
                document = cached.release_package_schema
        if path == 'standard/schema/record-package-schema.json':
            if cached.record_schema == '':
                raise CachedStandard.DoesNotExist
            else:
                document = cached.record_schema
        if path == 'standard/schema/versioned-release-schema.json':
            if cached.versioned_release_schema == '':
                raise CachedStandard.DoesNotExist
            else:
                document = cached.versioned_release_schema
    except CachedStandard.DoesNotExist:
        document = get_document_from_github_and_cache(
            repo, path, release, doctype
        )
    return document


def get_document_from_github_and_cache(repo, path, release, doctype='html'):
    document = get_document_from_github(repo, path, release, doctype)
    to_cache, created = CachedStandard.objects.get_or_create(tag_name=release)
    if path == 'standard/standard.md':
        to_cache.standard = document
    if path == 'standard/vocabulary.md':
        to_cache.vocabulary = document
    if path == 'standard/merging.md':
        to_cache.merging = document
    if path == 'standard/worked_example.md':
        to_cache.worked_example = document
    if path == 'standard/schema/release-schema.json':
        to_cache.release_schema = document
    if path == 'standard/schema/release-package-schema.json':
        to_cache.release_package_schema = document
    if path == 'standard/schema/record-package-schema.json':
        to_cache.record_schema = document
    if path == 'standard/schema/versioned-release-schema.json':
        to_cache.versioned_release_schema = document
    to_cache.save()
    return document


class LatestView(RedirectView):
    permanent = False

    def get_redirect_url(self, *args, **kwargs):
        # TODO: change to get tag directly from the git repo
        # TODO: redirect directly to first page of this export
        try:
            latest_release = LatestVersion.objects.get().tag_name
        except MultipleObjectsReturned:
            latest_release = 'master'
        kwargs.update({'release': latest_release})
        return reverse('standard-root', kwargs=kwargs)


class StandardRedirectView(RedirectView):

    # TODO: what about legacy tags where this URL is valid?
    def get_redirect_url(self, *args, **kwargs):
        # TODO: find the initial path
        dummy_path = 'standard/intro'
        kwargs.update({'lang': self.lang, 'path': dummy_path})
        return reverse('standard', kwargs=kwargs)


class StandardRootView(StandardRedirectView):
    permanent = False
    DEFAULT_LANG = 'en'

    def get(self, request, *args, **kwargs):
        self.lang = self.DEFAULT_LANG
        return super(StandardRootView, self).get(request, *args, **kwargs)


class StandardLangView(StandardRedirectView):
    permanent = False

    def get(self, request, *args, **kwargs):
        self.lang = kwargs.get('lang')
        return super(StandardLangView, self).get(request, *args, **kwargs)


class StandardView(TemplateView):
    template_name = 'main/standard.html'
    git_sha_re = re.compile(r'^[0-9a-fA-F]{40}$')

    def get(self, request, *args, **kwargs):
        self.release = kwargs.get('release')
        # allow for child class to set this
        if not hasattr(self, 'is_commit_id'):
            self.is_commit_id = (self.git_sha_re.match(self.release) is not None)
        self.lang = kwargs.get('lang')
        self.path = kwargs.get('path')
        self.other_releases = []

        self.repo = StandardsRepo()
        self.repo.export_commit(self.release)
        self.real_release = self.repo.standardise_commit_name(self.release)

        self.html_prod = HTMLProducer(self.real_release)
        self.html_dir = self.html_prod.get_html_dir()

        cleaned_release = "master"

        ordered_tags = self.repo.get_ordered_tag_dicts()
        for tag in ordered_tags:
            if tag['name'] == self.release:
                cleaned_release = tag['name']
                self.current_release = tag
            else:
                self.other_releases.append(tag)

        if self.release == 'master':
            self.current_release = self.repo.get_master_tag_dict()

        if not self.is_commit_id and cleaned_release != self.release:
            # We didn't get a correct release request, so redirect
            return HttpResponseRedirect(reverse('latest'))
        else:
            return super(StandardView, self).get(request, *args, **kwargs)

    def get_context_data(self, *args, **kwargs):
        context = super(StandardView, self).get_context_data(*args, **kwargs)
        # get file path corresponding to self.path and put in context
        ssi_path = path.join(self.html_dir, self.lang, self.path) + '.html'
        if not path.exists(ssi_path):
            raise Http404
        # TODO: edit template to ignore some details and do {% ssi filepath %}
        # instead, but save the current template as some will be broken out to
        # support creating menus later on.

        context_dict = {
            'ssi_path': ssi_path,
            'latest_release': LatestVersion.objects.get(),
            'other_releases': self.other_releases,
            'site_unique_id': settings.SITE_UNIQUE_ID,
            'lang': self.lang,
            'file_path': self.path,
            'form': AuthenticationForm()
        }
        if self.is_commit_id:
            context_dict['commit'] = self.release
        else:
            context_dict['current_release'] = self.current_release

        context.update(context_dict)
        return context


class CommitView(StandardView):
    template_name = 'main/standard.html'

    def get(self, request, *args, **kwargs):
        self.is_commit_id = True
        return super(CommitView, self).get(request, *args, **kwargs)


class SchemaView(JSONResponseMixin, View):
    def get(self, request, *args, **kwargs):
        sn = kwargs.get('schema_name')
        release = kwargs.get('release')
        if release == 'standard':
            release = 'master'
            doc = get_document_from_github(
                repo=get_repo(),
                path='standard/schema/%s.json' % sn,
                release=release,
                doctype='json'
            )
        else:
            doc = get_document_from_cache(
                repo=get_repo(),
                path='standard/schema/%s.json' % sn,
                release=release,
                doctype='json'
            )
            if doc == '':
                # Set to blank json if no doc so docson has something to render
                doc = "{}"
        return HttpResponse(doc, content_type="application/json", status=200)


class SchemaCommitView(JSONResponseMixin, View):
    def get(self, request, *args, **kwargs):
        sn = kwargs.get('schema_name')
        commit = kwargs.get('commitid')
        doc = get_document_from_cache(repo=get_repo(),
                                      path='standard/schema/%s.json' % sn,
                                      release=commit,
                                      doctype='json')
        return HttpResponse(doc, content_type="application/json", status=200)
