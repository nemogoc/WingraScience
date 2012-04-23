import numpy as np
import email, imaplib
from datetime import datetime, timedelta
import re
import time
import base64
import sys
import os
import string

# fuzzywuzzy is a fuzzy string matching code from:
# https://github.com/seatgeek/fuzzywuzzy
# note that not really installing it here - just putting the code in locally
import fuzz
import process

class gage_results:
    # initialize the class
    def __init__(self,gage):
        self.gage = gage
        self.date = list()
        self.datenum = list()
        self.height = list()
        
class timezone_conversion_data:
    def __init__(self):
        # set the timezone-specific values -- currently applies to all measurements
        self.std_time_utc_offset = timedelta(hours = 5)
        self.dst_time_utc_offset = timedelta(hours = 4)
        self.dst_start_month = 3
        self.dst_start_day = 11
        self.dst_start_hour = 2
        self.dst_end_month = 11
        self.dst_end_day = 4
        self.dst_end_hour = 2

class email_reader:
    # initialize the class
    def __init__ (self,user,pwd_encoded,email_scope):
        self.name = 'crowdhydrology'
        self.user = user
        self.pwd = base64.b64decode(pwd_encoded)
        self.email_scope = email_scope
        self.data = dict()
        self.dfmt = '%a, %d %b %Y %H:%M:%S '
        self.outfmt = '%m/%d/%Y %H:%M:%S'
        # make a list of valid station IDs
        self.stations = ['ny1000','ny1001','ny1002','ny1003',
                         'ny1004','ny1005','ny1006','ny1007','ny1008']
        for i in self.stations:
            self.data[i] = gage_results(i)
        self.tzdata = timezone_conversion_data()


    # read the previous data from the CSV files
    def read_CSV_data(self):
    # loop through the stations
        for cg in self.stations:
            if os.path.exists('../data/' + cg + '.csv'):
                indat = np.genfromtxt('../data/' + cg + '.csv',dtype=None,delimiter=',',names=True)
                dates = np.atleast_1d(indat['Date_and_Time'])
                gageheight = np.atleast_1d(indat['Gage_Height_ft']) 
                datenum = np.atleast_1d(indat['POSIX_Stamp'])
                try:
                    len_indat = len(indat)
                    for i in xrange(len_indat):
                        self.data[cg].date.append(dates[i])
                        self.data[cg].height.append(gageheight[i])
                        self.data[cg].datenum.append(datenum[i])
                except:
                        self.data[cg].date.append(dates[0])
                        self.data[cg].height.append(gageheight[0])
                        self.data[cg].datenum.append(datenum[0])            
    # login in to the server
    def login(self):
        try:
            self.m = imaplib.IMAP4_SSL("imap.gmail.com")
            self.m.login(self.user,self.pwd)
            self.m.select("[Gmail]/All Mail")
        except:
            raise(LogonFail(self.user))
        
    # check for new messages
    def checkmail(self):
        # find only new messages
        # other options available 
        # (http://www.example-code.com/csharp/imap-search-critera.asp)
        resp, self.msgids = self.m.search(None, self.email_scope)

    # parse the new messages into new message objects
    def parsemail(self):
        tot_msgs = len(self.msgids[0].split())
        kmess = 0
        self.messages = list()
        for cm in self.msgids[0].split():
            kmess+=1
            kmrat = np.ceil(100*(kmess/float(tot_msgs)))
            if kmess == 0:
                rems = 0
            else:
                rems = np.remainder(100,kmess)
            if rems == 0:
                print '-',
                sys.stdout.flush()
            resp, data = self.m.fetch(cm, "(RFC822)")
            msg = email.message_from_string(data[0][1])
            if 'sms from' in msg['Subject'].lower():
                self.messages.append(email_message(msg['Date'],msg['Subject'],msg.get_payload()))
        print '-'
        
    # now parse the actual messages -- date and body
    def parsemsgs(self):
        # parse through all the messages
        for currmess in self.messages:
            # first the dates
            tmpdate = currmess.rawdate[:-5]
            currmess.date = datetime.strptime(tmpdate,self.dfmt)
            currmess.date = tz_adjust_EST_EDT(currmess.date,self.tzdata)
            currmess.dateout = datetime.strftime(currmess.date,self.outfmt)
            currmess.datestamp = time.mktime(datetime.timetuple(currmess.date)) 
            # now the message bodies
            cm = currmess.body 
            maxratio = 0
            maxrat_count = -99999
           # maxrat_line = -99999
            line = cm.lower()
            line = string.rstrip(line,line[string.rfind(line,'sent using sms-to-email'):])
            line = re.sub('(\r)',' ',line)
            line = re.sub('(\n)',' ',line)
            line = re.sub('(--)',' ',line)
            
            if (('ny' in line) or ('by' in line) or ('my' in line) or ('station' in line)):
                currmess.is_gage_msg = True
                # we will test the line, but we need to remove some terms using regex substitutions
                line = re.sub('(ny)','',line)
                line = re.sub('(by)','',line)
                line = re.sub('(my)','',line)
                line = re.sub('(station)','',line)
                line = re.sub('(water)','',line)
                line = re.sub('(level)','',line)
                line = re.sub('(#)','',line)
                # now get rid of the floating point values that should be the stage
                # using regex code from: http://stackoverflow.com/questions/385558/
                # python-and-regex-question-extract-float-double-value
                currmess.station_line = line
                line = re.sub("[+-]? *(?:\d+(?:\.\d*)|\.\d+)(?:[eE][+-]?\d+)?",'', line)
                
                for j,cs in enumerate(self.stations):
                    # get the similarity ratio
                    crat = fuzz.ratio(line,cs)
                    if crat > maxratio:
                        maxratio = crat
                        maxrat_count = j
                currmess.max_prox_ratio = maxratio    
                currmess.closest_station_match = maxrat_count
                
                # rip the float out of the line
                v = re.findall("[+-]? *(?:\d+(?:\.\d*)|\.\d+)(?:[eE][+-]?\d+)?", currmess.station_line)
                try:
                    currmess.gageheight = float(v[0])
                except:
                    continue

    # for the moment, just re-populate the entire data fields
    def update_data_fields(self):
        #mnfdebug ofpdebug = open('debug.dat','w')
        for cm in self.messages:
            if cm.is_gage_msg:
                if ((cm.gageheight > 0) and (cm.gageheight < 20)):
                    self.data[self.stations[cm.closest_station_match]].date.append(cm.date.strftime(self.outfmt))
                    self.data[self.stations[cm.closest_station_match]].datenum.append(cm.datestamp)
                    self.data[self.stations[cm.closest_station_match]].height.append(cm.gageheight)
                   #mnfdebug ofpdebug.write('%25s%20f%12f%12s\n' %(cm.date.strftime(self.outfmt),cm.datestamp,cm.gageheight,self.stations[cm.closest_station_match]))
        #mnfdebug ofpdebug.close()
    # write all data to CSV files            
    def write_all_data_to_CSV(self):
    # loop through the stations
        for cg in self.stations:
            ofp = open('../data/' + cg + '.csv','w')
            ofp.write('Date and Time,Gage Height (ft),POSIX Stamp\n')
            datenum = self.data[cg].datenum # POSIX time stamp fmt for sorting
            dateval = self.data[cg].date
            gageheight = self.data[cg].height
            outdata = np.array(zip(datenum,dateval,gageheight))
            unique_dates =np.unique(outdata[:,0])
            indies = np.searchsorted(outdata[:,0],unique_dates)
            final_outdata = outdata[indies,:]
            for i in xrange(len(final_outdata)):
                ofp.write(final_outdata[i,1] + ',' + str(final_outdata[i,2]) + ',' + str(final_outdata[i,0]) + '\n')
            ofp.close()
                
    # plot the results in a simple time series using Dygraphs javascript (no Flash ) option
    def plot_results_dygraphs(self):
        # loop through the stations
        for cg in self.stations:
            #datenum = self.data[cg].datenum ## depracated ##
            dateval = self.data[cg].date
            gageheight = self.data[cg].height
            header = ('<!DOCTYPE html>\n<html>\n' +
                      '  <head>\n' +
                      '    <meta http-equiv="X-UA-Compatible" content="IE=EmulateIE7; IE=EmulateIE9">\n' +
                      '    <!--[if IE]><script src="js/canvas/excanvas.js"></script><![endif]-->\n' +
                      '  </head>\n' +
                        '  <body>\n' +
                        "  "*2 + '<script src="js/graph/dygraph-combined.js" type="text/javascript"></script> \n'+
                        "  "*3 +  '<div id="graphdiv"></div>\n')
            middata = ' <script>\nvar Final_list = "Date, Gage Height at %s\\n";\n' %(cg); 
            for i,cd in enumerate(dateval):
                middata += ("  "*4 + 'Final_list += "' + 
                            cd + ',' + 
                            str(gageheight[i]) + '\\n";\n')
            footer = ("  "*4 + 'g = new Dygraph(\n' +
                    "  "*4 + 'document.getElementById("graphdiv"),\n' +
                    "  "*4 + 'Final_list,\n' + 
                    "  "*4 + '{   title: "Hydrograph at '+cg+ '",\n'  + 
                    "  "*4 + "labelsDivStyles: { 'textAlign': 'right' },\n" +
                    "  "*4 + 'showRoller: true,\n' + 
                    "  "*4 + "xValueFormatter: Dygraph.dateString_,\n" + 
                    "  "*4 + "xTicker: Dygraph.dateTicker,\n" +
                    "  "*4 + "labelsSeparateLines: true,\n" +
                    "  "*4 + "labelsKMB: true,\n" +
                    "  "*4 + "drawXGrid: false,\n" + 
                    "  "*4 + " width: 640,\n" + 
                    "  "*4 + "height: 300,\n" +
                    "  "*4 + "xlabel: 'Date',\n" + 
                    "  "*4 + "ylabel: 'Gage Height (ft.)',\n" + 
                    "  "*4 + "strokeWidth: 2,\n" + 
                    "  "*4 + "showRangeSelector: true\n"  +
                    "  "*4 + "}\n" +
                    "  "*4 + ");\n" +
                    "</script>\n</body>\n</html>\n")

            
            self.data[cg].charttext = header + middata + footer
            ofp = open('../charts/' + cg + '_dygraph.html','w')
            ofp.write(self.data[cg].charttext)
            ofp.close()

def tz_adjust_EST_EDT(cdateUTC,tzdata):
    # make the adjustment, based on 2011 and onward, EST/EDT schedule
    # this must be adjusted for other timezones
    # details of the schedule can be found at http://www.timeanddate.com/worldclock/
    cmonth = cdateUTC.month
    cday = cdateUTC.day
    chour = cdateUTC.hour
    cmin = cdateUTC.minute
    cyear= cdateUTC.year
    dst_start = datetime(cyear,tzdata.dst_start_month,tzdata.dst_start_day,tzdata.dst_start_hour)
    dst_end = datetime(cyear,tzdata.dst_end_month,tzdata.dst_end_day,tzdata.dst_end_hour)
    # see if the current time in UTC falls within DST or not and adjust accordingly
    if ((cdateUTC >= dst_start) and (cdateUTC <= dst_end)):
        cdate = cdateUTC - tzdata.dst_time_utc_offset
    else:
        cdate = cdateUTC - tzdata.std_time_utc_offset
    return cdate

class email_message:
    # initialize an individual message
    def __init__(self,date,header,txt):
        self.is_gage_msg = False
        self.header=header
        self.body=txt
        self.rawdate = date
        self.date = ''
        self.dateout = ''
        self.max_prox_ratio = 0
        self.closest_station_match = ''
        self.station_line = ''
        self.gageheight = -99999
        

        
           
# ####################### #
# Error Exception Classes #        
# ####################### #
# -- cannot log on
class LogonFail(Exception):
    def __init__(self,username):
        self.name=username
    def __str__(self):
        return('\n\nLogin Failed: \n' +
               'Cannot log on ' + self.name)
# -- cannot connect to spreadsheet
class SheetConnect(Exception):
    def __init__(self,key):
        self.key=key
    def __str__(self):
        return('\n\nCannot connec to spreadsheet with key:\n' + self.key)
    