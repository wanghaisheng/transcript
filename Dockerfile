
FROM ubuntu:15.10

#[wanghs@db2 debian]$ docker build -t dc/pdf2htmlex .
#[wanghs@db2 debian]$ docker run --rm --name pdf2htmlex-programming-demo -it -v $(pwd):/tmp dc/pdf2htmlex /bin/bash




ADD sources.list /etc/apt/sources.list

#
#Install git and all dependencies
#
RUN  apt-get -y update &&  apt-get install -qq git cmake autotools-dev libjpeg-dev libtiff5-dev libpng12-dev libgif-dev libxt-dev autoconf automake libtool bzip2 libxml2-dev libuninameslist-dev libspiro-dev libpango1.0-dev libcairo2-dev chrpath uuid-dev uthash-dev    python3.4   python3.4-dev python3-pip  libfreetype6 libqtcore4 libqtgui4 ttfautohint poppler-data libjpeg-dev 


#
#Clone the pdf2htmlEX fork of fontforge
#compile it
#
RUN git clone https://github.com/coolwanglu/fontforge.git fontforge.git
RUN cd fontforge.git && git checkout pdf2htmlEX && ./autogen.sh && ./configure && make V=1 &&  make install

#
#Install poppler utils
#
RUN  apt-get install -qq libpoppler-glib-dev libpoppler-private-dev

#
#Clone and install the pdf2htmlEX git repo



RUN  \
    cd /tmp \ 
    && apt-get install wget \
    && wget https://github.com/coolwanglu/pdf2htmlEX/archive/v0.14.6.tar.gz \
    && tar xvf v0.14.6.tar.gz \
    && cd pdf2htmlEX* 
RUN cd  /tmp/pdf2htmlEX*   &&  cmake . && make &&  make install



# install  transcript  
RUN apt-get install -y libxml2-dev libxslt1-dev

RUN  \
     cd /tmp \ 
     && git clone  https://github.com/fmalina/transcript \
    && cd transcript \
#    && pip3 install  -i https://pypi.tuna.tsinghua.edu.cn/simple lxml cssselect freetype-py
    && pip3 install -i https://pypi.tuna.tsinghua.edu.cn/simple  -r requirements.txt 

## css inline html
RUN pip3 install  -i https://pypi.tuna.tsinghua.edu.cn/simple  pynliner

    
VOLUME /pdf
WORKDIR /pdf

CMD ["pdf2htmlEX"]