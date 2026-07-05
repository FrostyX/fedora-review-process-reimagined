FROM registry.fedoraproject.org/fedora:43
MAINTAINER frostyx@email.cz

RUN dnf -y update && \
    dnf -y install htop \
                   which \
                   wget \
                   vim \
                   cgit \
                   python3-rpkg \
                   python3-copr \
                   rpkg \
                   tini \
                   uv \
                   && \
    dnf -y install dist-git && \
    dnf clean all

RUN rm /etc/httpd/conf.d/ssl.conf

# RUN echo "AliasMatch \"/repo(/.*)/md5(/.*)\" \"/var/lib/dist-git/cache/lookaside\\$1\\$2\"" >> /etc/httpd/conf.d/dist-git/lookaside-copr.conf && \
#     echo "Alias /repo/ /var/lib/dist-git/cache/lookaside/" >>  /etc/httpd/conf.d/dist-git/lookaside-copr.conf

RUN sed -i 's/Listen 80/Listen 5080/' /etc/httpd/conf/httpd.conf
RUN sed -i 's/^cache-size=1000$/# cache-size=1000/' /etc/cgitrc
RUN sed -i 's#^download_url = .*#download_url = http://localhost:5080/repo/pkgs/%(name)s/%(filename)s/%(hash)s/%(filename)s#' /etc/rpkg.conf
RUN echo 'scan-path=/var/lib/dist-git/git/rpms' | tee -a /etc/cgitrc


# command: /usr/sbin/httpd -DFOREGROUND
