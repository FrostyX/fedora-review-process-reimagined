# Fedora Review Process Reimagined


## Devel

```
docker-compose up -d
docker-compose exec -it distgit bash
cd /opt
FORGEJO_TOKEN="10ff559b9e1dc5c11992602090e9e29dbe164185" PYTHONPATH=. python review_reimagined/review-reimagined-import-to-distgit.py
```


```
rpkg clone -a file:///var/lib/dist-git/git/rpms/bbb.git /tmp/foo
cd /tmp/foo
git checkout rawhide
rpkg srpm
```
