# The builder image of the Linux build: Ubuntu 22.04 with everything a build needs from outside, made once by
# packaging/build_linux.sh (again only when this file or builder_setup.sh changes). See packaging/PACKAGING.md.
FROM docker.io/library/ubuntu:22.04
COPY builder_setup.sh /setup/builder_setup.sh
RUN bash /setup/builder_setup.sh
