from django.urls import path, re_path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("save", views.save, name="save"),
    path("run", views.run, name="run"),
    path("api/status", views.api_status, name="api_status"),
    re_path(r"^downloads/(?P<relative_path>.+)$", views.download_asset, name="download_asset"),
]
