#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Created on Tue 1 May 2023 @author: Ian Jefferson G4IXT.  TinySA Ultra GUI programme using Qt5 and PyQt.

This code attempts to replicate some of the TinySA Ultra on-screen commands and to provide PC control.
Development took place on Kubuntu 22.04LTS with Python 3.9 and PyQt5 using Spyder in Anaconda.

TinySA and TinySA Ultra are trademarks of Erik Kaashoek and are used with permission.

TinySA commands are based on Erik's Python examples: http://athome.kaashoek.com/tinySA/python/

The serial communication commands are based on the Python NanoVNA/TinySA Toolset of Martin Ho-Ro:
https://github.com/Ho-Ro

"""
import os
import time
import logging
import numpy as np
from PyQt5 import QtWidgets, QtCore, QtGui
from PyQt5.QtCore import pyqtSlot, pyqtSignal, QRunnable, QObject, QThreadPool, Qt, QTimer
from PyQt5.QtWidgets import QMessageBox, QDataWidgetMapper
from PyQt5.QtSql import QSqlDatabase, QSqlRelation, QSqlRelationalTableModel, QSqlRelationalDelegate
import pyqtgraph
import QtTinySpectrum  # the GUI
import QtTSApreferences  # GUI preferences dialogue
import struct
import serial
from serial.tools import list_ports

#  For 3D
import pyqtgraph.opengl as pyqtgl

logging.basicConfig(format="%(message)s", level=logging.INFO)
threadpool = QThreadPool()
basedir = os.path.dirname(__file__)

# pyqtgraph pens
red = pyqtgraph.mkPen(color='r', width=1.0)
yellow = pyqtgraph.mkPen(color='y', width=1.0)
white = pyqtgraph.mkPen(color='w', width=1.0)
magenta = pyqtgraph.mkPen(color='m', width=1.0)
cyan = pyqtgraph.mkPen(color='c', width=1.0)
red_dash = pyqtgraph.mkPen(color='r', width=0.5, style=QtCore.Qt.DashLine)
blue_dash = pyqtgraph.mkPen(color='b', width=0.5,  style=QtCore.Qt.DashLine)

###############################################################################
# classes


class analyser:
    def __init__(self):
        self.usb = None
        self._frequencies = None
        self.sweeping = False
        self.signals = WorkerSignals()
        self.signals.result.connect(self.sigProcess)
        self.signals.result3D.connect(self.updateTimeSpectrum)
        self.signals.finished.connect(self.threadEnds)
        self.timeout = 1
        self.scanCount = 1
        self.runTimer = QtCore.QElapsedTimer()
        self.scale = 174
        self.scanMemory = 50
        self.scan3D = False
        self.surface = None
        self.vGrid = None
        self.checkUSB = QTimer()
        self.checkUSB.timeout.connect(self.isConnected)
        self.resBW = ['0.2', '1', '3', '10', '30', '100', '300', '600', '850']

    @property
    def frequencies(self):
        return self._frequencies

    def openPort(self):
        self.dev = None
        # TinySA Ultra resolution bandwidth filters in kHz
        # Get tinysa device (port) automatically using hardware ID
        VID = 0x0483  # 1155
        PID = 0x5740  # 22336
        device_list = list_ports.comports()
        for x in device_list:
            if x.vid == VID and x.pid == PID:
                self.dev = x.device
                logging.info(f'Found TinySA on {self.dev}')
        if self.dev is None:
            activeButtons(False)  # do not trigger serial commands
            ui.version.setText('TinySA not found')
            if not self.checkUSB.isActive():
                logging.info('TinySA not found')
        if self.dev and self.usb is None:  # TinySA was found but serial comms not open
            try:
                self.usb = serial.Serial(self.dev)
                logging.info(f'Serial port open: {self.usb.isOpen()}')
            except serial.SerialException:
                logging.info('serial port exception')
                popUp('Serial Port Exception', 'OK', QMessageBox.Critical)
        if self.dev and self.usb:
            self.clearBuffer()
            self.initialise()

    def closePort(self):
        if self.usb:
            self.usb.close()
            logging.info(f'Serial port open: {self.usb.isOpen()}')
            self.usb = None

    def isConnected(self):
        # triggered by self.checkUSB QTimer - if tinySA wasn't found checks repeatedly for device, i.e.'hotplug'
        if self.dev is None:
            self.openPort()
        else:
            self.checkUSB.stop()

    def initialise(self):
        # show hardware information in GUI
        i = 0
        hardware = ''
        while hardware[:6] != 'tinySA' and i < 3:
            hardware = self.version()
            logging.info(f'{hardware}')
            i += 1
            time.sleep(0.1)
        # hardware = 'basic'  # used for testing
        if hardware[:7] == 'tinySA4':  # It's an Ultra
            self.tinySA4 = True
            ui.spur_box.setTristate(True)  # TinySA Ultra has 'auto', 'on' and 'off' setting for Spur
            ui.spur_box.setCheckState(QtCore.Qt.PartiallyChecked)
            self.spur(1)  # 1 = auto
        else:
            self.tinySA4 = False
            self.scale = 128
            self.resBW = self.resBW[2:8]  # TinySA Basic has fewer resolution bandwidth filters
            ui.spur_box.setTristate(False)  # TinySA Basic has only 'on' and 'off' setting for Spur'
            ui.spur_box.setChecked(True)
            self.spur(2)  # 2 = on

        # Basic has no lna
        ui.lna_label.setVisible(self.tinySA4)
        ui.lna_box.setVisible(self.tinySA4)
        ui.lna_box.setEnabled(self.tinySA4)

        # set the frequency band & rbw comboboxes to suit detected hardware
        setPreferences()

        self.resBW.insert(0, 'auto')
        ui.rbw_box.addItems(self.resBW)
        ui.rbw_box.setCurrentIndex(len(self.resBW)-4)

        activeButtons(True)  # enable ui components that trigger serial commands

        # update centre freq, span, auto points and graph for the start/stop freqs loaded from database
        self.freq_changed()
        ui.graphWidget.setXRange(ui.start_freq.value(), ui.stop_freq.value())

        if self.tinySA4:
            self.lna()  # LNA off at first run

        # show hardware information in GUI
        ui.battery.setText(self.battery())
        ui.version.setText(hardware)

        # update trace and marker settings from the database.  1 = last saved (default) settings
        S1.dLoad(1)
        S2.dLoad(1)
        S3.dLoad(1)
        S4.dLoad(1)

        #  set each marker to a different colour
        S2.vline.setPen(color='m', width=0.75, style=QtCore.Qt.DashLine)
        S2.vline.label.setColor('m')
        S3.vline.setPen(color='c', width=0.75, style=QtCore.Qt.DashLine)
        S3.vline.label.setColor('c')
        S4.vline.setPen(color='w', width=0.75, style=QtCore.Qt.DashLine)
        S4.vline.label.setColor('w')

        # connect the rbw & frequency boxes here or it causes startup index errors when they are populated
        ui.rbw_box.currentIndexChanged.connect(tinySA.setRBW)
        ui.start_freq.editingFinished.connect(lambda: self.freq_changed(False))
        ui.stop_freq.editingFinished.connect(lambda: self.freq_changed(False))
        ui.centre_freq.editingFinished.connect(lambda: self.freq_changed(True))
        ui.span_freq.editingFinished.connect(lambda: self.freq_changed(True))
        ui.band_box.activated.connect(band_changed)

    def scan(self):  # called by 'run' button
        self.scan3D = ui.Enabled3D.isChecked()
        if self.usb is not None:
            if self.sweeping:  # if it's running, stop it
                self.sweeping = False  # tells the measurement thread to stop once current scan complete
                ui.scan_button.setEnabled(False)  # prevent repeat presses of 'stop'
                ui.run3D.setEnabled(False)
            else:
                try:  # start measurements
                    self.scanCount = 1
                    startF = ui.start_freq.value()*1e6
                    stopF = ui.stop_freq.value()*1e6
                    self.set_frequencies()
                    self.clearBuffer()
                    self.setRBW()  # fetches rbw value from the GUI combobox
                    self.sweepTimeout(startF, stopF)
                    activeButtons(False)
                    self.runButton('Stop')
                    self.startMeasurement(startF, stopF)  # runs measurement in separate thread
                except serial.SerialException:
                    self.dev = None
                    self.closePort()
        else:
            popUp('TinySA not found', 'OK', QMessageBox.Critical)

    def startMeasurement(self, startF, stopF):
        self.sweep = Worker(self.measurement, startF, stopF)  # workers are auto-deleted when thread stops
        self.sweeping = True
        self.sweepresults = np.full((self.scanMemory, self.points), -100, dtype=float)
        if ui.Enabled3D.isChecked():
            tinySA.createTimeSpectrum()
            self.reset3D()
        threadpool.start(self.sweep)

    def serialSend(self, command):
        self.clearBuffer()
        self.usb.timeout = 1
        logging.debug(command)
        self.usb.write(command)
        self.usb.read_until(b'ch> ')  # skip command echo and prompt

    def serialQuery(self, command):
        self.clearBuffer()
        self.usb.timeout = 1
        logging.debug(command)
        self.usb.write(command)
        self.usb.read_until(command + b'\n')  # skip command echo
        response = self.usb.read_until(b'ch> ')
        logging.debug(response)
        return response[:-6].decode()  # remove prompt

    def set_frequencies(self):
        startF = ui.start_freq.value()*1e6  # freq in Hz
        stopF = ui.stop_freq.value()*1e6
        self.points = ui.points_box.value()
        # creates a numpy array of equi-spaced freqs in Hz (but doesn't set it on the tinySA)
        self._frequencies = np.linspace(startF, stopF, self.points, dtype=int)
        logging.debug(f'frequencies = {self._frequencies}')

    def freq_changed(self, centre=False):
        if centre:
            startF = ui.centre_freq.value()-ui.span_freq.value()/2
            stopF = ui.centre_freq.value()+ui.span_freq.value()/2
            ui.start_freq.setValue(startF)
            ui.stop_freq.setValue(stopF)
        else:
            startF = ui.start_freq.value()  # freq in MHz
            stopF = ui.stop_freq.value()
            if startF > stopF:
                stopF = startF
                ui.stop_freq.setValue(stopF)
            ui.centre_freq.setValue(startF + (stopF - startF) / 2)
            ui.span_freq.setValue(stopF - startF)
        ui.graphWidget.setXRange(startF, stopF)
        self.setPoints()
        self.setRBW()
        self.set_frequencies()
        command = f'sweep start {startF * 1e6}\r'.encode()
        self.serialSend(command)
        command = f'sweep stop {stopF * 1e6}\r'.encode()
        self.serialSend(command)
        # do we need to submit dwm?

    def setRBW(self):
        if ui.rbw_box.currentIndex() == 0:  # can't calculate Points because we don't know what the RBW will be
            self.rbw = 'auto'
            ui.points_auto.setChecked(False)
            ui.points_auto.setEnabled(False)
        else:
            self.rbw = ui.rbw_box.currentText()  # ui values are discrete ones in kHz
            logging.debug(f'self rbw = {self.rbw}')
            self.setPoints()
            ui.points_auto.setEnabled(True)
        rbw_command = f'rbw {self.rbw}\r'.encode()
        self.serialSend(rbw_command)

    def setPoints(self):  # what if span = 0?
        if ui.points_auto.isChecked():
            self.rbw = ui.rbw_box.currentText()
            if preferences.bestPoints.isChecked():
                points = int((ui.span_freq.value()*1000)/(float(self.rbw)/3))  # best power accuracy; freq in kHz
            else:
                points = int((ui.span_freq.value()*1000)/(float(self.rbw)/2))  # normal power accuracy; freq in kHz
            logging.debug(f'points = {points}')
            if points > preferences.maxPoints.value():
                points = preferences.maxPoints.value()
            if points < preferences.minPoints.value():
                points = preferences.minPoints.value()
            ui.points_box.setValue(points)
        # number of points changed so must repopulate the frequencies array & set the marker freq indexes to suit
        self.set_frequencies()
        S1.setDiscrete()
        S2.setDiscrete()
        S3.setDiscrete()
        S4.setDiscrete()

    def clearBuffer(self):
        self.usb.timeout = 1
        while self.usb.inWaiting():
            self.usb.read_all()  # keep the serial buffer clean
            time.sleep(0.1)

    def sweepTimeout(self, f_low, f_high):  # freqs are in Hz
        if self.rbw == 'auto':
            # rbw auto setting from tinySA: ~7 kHz per 1 MHz scan frequency span
            rbw = (f_high - f_low) * 7e-6
        else:
            rbw = float(self.rbw)
        # lower / upper limit
        if rbw < float(self.resBW[1]):
            rbw = float(self.resBW[1])
        elif rbw > float(self.resBW[-1]):
            rbw = float(self.resBW[-1])
        # timeout can be very long - use a heuristic approach
        # 1st summand is the scanning time, 2nd summand is the USB transfer overhead
        timeout = ((f_high - f_low) / 20e3) / (rbw ** 2) + self.points / 500
        if (ui.spur_box.checkState() == 1 and f_high > 8 * 1e8) or ui.spur_box.checkState() == 2:
            timeout *= 2  # scan time doubles with spur on or spur auto above 800 MHz
        # transfer is done in blocks of 20 points, this is the timeout for one block
        self.timeout = timeout * 20 / self.points + 1  # minimum is 1 second
        logging.debug(f'sweepTimeout = {self.timeout:.2f} s')

    def measurement(self, f_low, f_high):  # runs in a separate thread
        self.threadRunning = True
        firstSweep = True
        while self.sweeping:
            try:
                self.usb.timeout = self.timeout
                scan_command = f'scanraw {int(f_low)} {int(f_high)} {int(self.points)}\r'.encode()
                self.usb.write(scan_command)
                index = 0
                self.usb.read_until(scan_command + b'\n{')  # skip command echo
                dataBlock = ''
                self.sweepresults[0] = self.sweepresults[1]  # populate each sweep with previous sweep as default
                while dataBlock != b'}ch' and index < self.points:  # if dataBlock is '}ch' it's reached the end of the scan points
                    dataBlock = (self.usb.read(3))  # read a block of 3 bytes of data
                    logging.debug(f'dataBlock: {dataBlock}\n')
                    if dataBlock != b'}ch':
                        logging.debug(f'index {index} elapsed time = {self.runTimer.nsecsElapsed()/1e6}')
                        c, data = struct.unpack('<' + 'cH', dataBlock)
                        logging.debug(f'dataBlock: {dataBlock} data: {data}\n')
                        dBm_power = (data / 32) - self.scale  # scale 0..4095 -> -128..-0.03 dBm
                        self.sweepresults[0, index] = dBm_power
                        if index // 20 == index / 20 or index == (self.points - 1):
                            self.signals.result.emit(self.sweepresults)
                        index += 1
                    logging.debug(f'level = {dBm_power}dBm')
                self.usb.read(2)  # discard the command prompt
                self.signals.result3D.emit(self.sweepresults)  # update 3D only once per sweep, for performance reasons
                if firstSweep:
                    # populate entire scan memory with first sweep as default starting point
                    self.sweepresults = np.full((self.scanMemory, self.points), self.sweepresults[0], dtype=float)
                    firstSweep = False
                # results row 0 is now full: roll it down 1 row ready for the next sweep to be stored at row 0
                self.sweepresults = np.roll(self.sweepresults, 1, axis=0)
                self.scanCount += 1
            except serial.SerialException:
                logging.info('serial port exception')
                self.sweeping = False
        self.threadRunning = False
        self.signals.finished.emit()

    def threadEnds(self):
        self.runButton('Run')
        activeButtons(True)

    def sigProcess(self, signaldBm):  # signaldBm is emitted from the worker thread
        if ui.avgSlider.value() > self.scanCount:  # slice using use scanCount to stop default values swamping average
            signalAvg = np.average(signaldBm[:self.scanCount, ::], axis=0)
        else:
            signalAvg = np.average(signaldBm[:ui.avgSlider.value(), ::], axis=0)
        signalMax = np.amax(signaldBm[:self.scanMemory, ::], axis=0)
        signalMin = np.amin(signaldBm[:self.scanMemory, ::], axis=0)
        options = {'Normal': signaldBm[0], 'Average': signalAvg, 'Max': signalMax, 'Min': signalMin}
        S1.updateGUI(options.get(S1.traceType))
        S2.updateGUI(options.get(S2.traceType))
        S3.updateGUI(options.get(S3.traceType))
        S4.updateGUI(options.get(S4.traceType))

    def createTimeSpectrum(self):
        x = np.arange(start=0, stop=self.scanMemory, step=1)  # the time axis depth
        y = np.arange(start=0, stop=self.points)  # the frequency axis width
        z = self.sweepresults  # the measurement axis heights in dBm
        logging.debug(f'z = {z}')
        if self.surface:  # if 3D spectrum exists, clear it
            ui.openGLWidget.clear()
        self.surface = pyqtgl.GLSurfacePlotItem(x=-x, y=y, z=z, shader='heightColor',
                                                computeNormals=ui.glNormals.isChecked(), smooth=ui.glSmooth.isChecked())

        #  for each colour, map = pow(z * colorMap[0] + colorMap[1], colorMap[2])
        self.surface.shader()['colorMap'] = np.array([ui.rMulti.value(),      # red   [0]
                                                      ui.rConst.value(),      # red   [1]
                                                      ui.rExponent.value(),   # red   [2]
                                                      ui.gMulti.value(),      # green [3]
                                                      ui.gConst.value(),      # green [4]
                                                      ui.gExponent.value(),   # green [5]
                                                      ui.bMulti.value(),      # blue  [6]
                                                      ui.bConst.value(),      # blue  [7]
                                                      ui.gExponent.value()])  # blue  [8]

        self.surface.translate(16, -self.points/40, -8)  # front/back, left/right, up/down
        self.surface.scale(self.points/1250, 0.05, 0.1, local=True)
        ui.openGLWidget.addItem(self.surface)

        # Add a vertical grid to the 3D view
        self.vGrid = pyqtgl.GLGridItem(glOptions='translucent')
        self.vGrid.setSize(x=12, y=self.points/20, z=1)
        self.vGrid.rotate(90, 0, 1, 0)
        self.vGrid.setSpacing(1, 1, 2)
        self.vGrid.setColor('y')
        if ui.grid.isChecked():
            ui.openGLWidget.addItem(self.vGrid)

    def updateTimeSpectrum(self, results):
        if ui.Enabled3D.isChecked():
            z = results + 120  # Surface plot height shader needs positive numbers so convert from dBm to dBf
            logging.debug(f'z = {z}')
            self.surface.setData(z=z)
            params = ui.openGLWidget.cameraParams()
            logging.debug(f'camera {params}')

    def orbit3D(self, sign, azimuth=True):  # orbits the camera around the 3D plot
        degrees = ui.rotateBy.value()
        if azimuth:
            ui.openGLWidget.orbit(sign*degrees, 0)  # sign controls direction and is +1 or -1
        else:
            ui.openGLWidget.orbit(0, sign*degrees)

    def axes3D(self, sign, axis):  # shifts the plot along one of its 3 axes - time, frequency, signal
        pixels = ui.panBy.value()
        options = {'X': (pixels*sign, 0, 0), 'Y': (0, pixels*sign, 0), 'Z': (0, 0, pixels*sign)}
        s = options.get(axis)
        ui.openGLWidget.pan(s[0], s[1], s[2], relative='global')

    def reset3D(self):  # sets the 3D view back to the starting point
        ui.openGLWidget.reset()
        self.orbit3D(135, 'X')
        ui.openGLWidget.pan(0, 0, -10, relative='global')
        self.zoom3D()

    def grid(self, sign):  # moves the grid backwards and forwards on the time axis
        step = ui.rotateBy.value()
        if ui.grid.isChecked():
            self.vGrid.translate(step*sign, 0, 0)

    def zoom3D(self):  # zooms the camera in and out
        zoom = ui.zoom.value()
        ui.openGLWidget.setCameraParams(distance=zoom)

    def runButton(self, action):
        # Update the Run/Stop buttons' text and colour
        ui.scan_button.setText(action)
        ui.run3D.setText(action)
        if action == 'Stopping':
            ui.scan_button.setStyleSheet('background-color: yellow')
            ui.run3D.setStyleSheet('background-color: yellow')
        else:
            ui.scan_button.setStyleSheet('background-color: white')
            ui.run3D.setStyleSheet('background-color: white')
            ui.scan_button.setEnabled(True)
            ui.run3D.setEnabled(True)
            # ui.battery.setText(self.battery())

    def pause(self):
        # pauses the sweeping in either input or output mode
        command = 'pause\r'.encode()
        self.serialSend(command)

    def resume(self):
        # resumes the sweeping in either input or output mode
        command = 'resume\r'.encode()
        self.serialSend(command)

    def reset(self):
        # not yet found any detail for what is actually reset
        command = 'reset\r'.encode()
        self.serialSend(command)

    def battery(self):
        command = 'vbat\r'.encode()
        vbat = self.serialQuery(command)
        return vbat

    def version(self):
        command = 'version\r'.encode()
        version = self.serialQuery(command)
        return version

    def spur(self, sType=0):
        options = {0: 'spur off\r'.encode(), 1: 'spur auto\r'.encode(), 2: 'spur on\r'.encode()}
        command = options.get(sType)
        self.serialSend(command)
        if sType == 1:
            ui.spur_box.setText('Auto')
        else:
            ui.spur_box.setText('')

    def lna(self):
        if ui.lna_box.isChecked():
            command = 'lna on\r'.encode()
            ui.atten_auto.setEnabled(False)  # attenuator and lna are switched so mutually exclusive
            ui.atten_auto.setChecked(False)
            ui.atten_box.setEnabled(False)
            ui.atten_box.setValue(0)
        else:
            command = 'lna off\r'.encode()
            ui.atten_auto.setEnabled(True)
            ui.atten_auto.setChecked(True)
        self.serialSend(command)


class display:
    def __init__(self, name, pen):
        self.name = name
        self.trace = ui.graphWidget.plot([], [], name=name, pen=pen, width=1)
        # self.trace.hide()
        self.traceType = 'Normal'  # Normal, Average, Max, Min
        self.markerType = 'Normal'  # Normal, Delta; Peak
        self.vline = ui.graphWidget.addLine(88, 90, movable=True, name=name,
                                            pen=pyqtgraph.mkPen('y', width=0.5, style=QtCore.Qt.DashLine),
                                            label="{value:.2f}")
        self.hline = ui.graphWidget.addLine(y=0, movable=False, pen=red_dash, label='',
                                            labelOpts={'position': 0.025, 'color': ('w')})
        self.fIndex = 0  # index of current marker freq in the frequencies array
        self.dIndex = 0  # the difference between this marker and Reference Marker (M1)

    def setDiscrete(self):
        # set marker to the discrete freq near the posn it has been dragged to (if within the sweep range)
        if self.vline.value() >= ui.start_freq.value() and self.vline.value() <= ui.stop_freq.value():
            try:
                for i in range(tinySA.points):
                    if tinySA.frequencies[i] / 1e6 >= self.vline.value():
                        self.vline.setValue(tinySA.frequencies[i] / 1e6)
                        self.fIndex = i  # marker freq index is now set
                        if self.markerType == 'Delta':
                            self.dIndex = self.fIndex - S1.fIndex  # save delta index this marker vs Reference marker
                        return
            except AttributeError:
                return

    def mStart(self):
        # set marker to the sweep start frequency
        if self.guiRef(0).isChecked():
            self.fIndex = 0
            self.vline.setValue(ui.start_freq.value())

    def mSpread(self):
        # spread markers equally across scan range
        if self.guiRef(0).isChecked():
            self.fIndex = int(0.2 * int(self.name) * ui.points_box.value())
            self.vline.setValue(tinySA.frequencies[self.fIndex] / 1e6)

    def mType(self):
        self.markerType = self.guiRef(1).currentText()
        if self.markerType == 'Delta':
            self.dIndex = self.fIndex - S1.fIndex
        logging.debug(f'marker type = {self.markerType}')

    def mPeak(self, signal):
        # marker peak tracking
        peaks = np.argsort(-signal)  # finds the indices of the peaks in a copy of signal array; indices sorted desc
        if signal[peaks[0]] >= ui.mPeak.value():  # largest peak value is above the threshold set in GUI
            options = {'Peak1': peaks[0], 'Peak2': peaks[1], 'Peak3': peaks[2], 'Peak4': peaks[3]}
            self.fIndex = options.get(self.markerType)
            self.vline.setValue(tinySA.frequencies[self.fIndex] / 1e6)
            logging.debug(f'peaks = {peaks[:4]}')

    def mDelta(self):  # delta marker locking to reference marker S1
        if self.markerType == 'Delta':
            self.fIndex = S1.fIndex + self.dIndex
            S1.vline.setPen(color='y', width=1.0)
            if self.fIndex < 0:  # delta marker is now below sweep range
                self.fIndex = 0
            if self.fIndex > tinySA.points - 1:  # delta marker is now above sweep range
                self.fIndex = tinySA.points - 1
            self.vline.setValue(tinySA.frequencies[self.fIndex] / 1e6)

    # The set of 4 functions below are needed until I understand how to make dataWidgetMapper work with comboboxes
    def mData(self, setting, saving=True):
        # markers.tm.setFilter('display = "S1"')  # this is the syntax needed for setFilter
        markers.tm.setFilter('display = ' + str(self.name) + ' AND setting = ' + str(setting))
        markers.tm.select()
        record = markers.tm.record(0)
        if saving:
            record.setValue('frequency', float(self.vline.value()))
            record.setValue('type', self.markerType)
            markers.tm.setRecord(0, record)
        else:
            self.vline.setValue(record.value('frequency'))
            self.markerType = record.value('type')
            self.guiRef(1).setCurrentText(self.markerType)
            logging.debug(f'marker f = {record.value("frequency")}')
            self.vline.label.setText(f'M{self.vline.name()} {tinySA.frequencies[self.fIndex]/1e6:.3f}MHz', color='y')
            self.vline.label.setMovable(True)
            self.setDiscrete()
            self.mEnable()

    def tData(self, setting, saving=True):
        traces.tm.setFilter('display = ' + str(self.name) + ' AND setting = ' + str(setting))
        traces.tm.select()
        record = traces.tm.record(0)
        if saving:
            record.setValue('type', self.traceType)
            traces.tm.setRecord(0, record)
        else:
            self.traceType = record.value('type')
            self.guiRef(3).setCurrentText(self.traceType)

    def dSave(self, setting):
        self.tData(setting, True)
        self.mData(setting, True)  # true = saving

    def dLoad(self, setting):
        self.mData(setting, False)  # false = not saving but loading
        self.tData(setting, False)
        self.tEnable()
    # The set of 4 functions above are needed until understand how to make dataWidgetMapper work with comboboxes

    def guiRef(self, opt):
        guiFields = ({'1': ui.marker1, '2': ui.marker2, '3': ui.marker3, '4': ui.marker4},
                     {'1': ui.m1_type, '2': ui.m2_type, '3': ui.m3_type, '4': ui.m4_type},
                     {'1': ui.trace1, '2': ui.trace2, '3': ui.trace3, '4': ui.trace4},
                     {'1': ui.t1_type, '2': ui.t2_type, '3': ui.t3_type, '4': ui.t4_type})
        Ref = guiFields[opt].get(self.name)
        return Ref

    def tType(self):
        self.traceType = self.guiRef(3).currentText()

    def mEnable(self):  # show or hide a marker
        # if mkr.isChecked():
        if self.guiRef(0).isChecked():
            self.vline.show()
        else:
            self.vline.hide()
        checkboxes.dwm.submit()

    def hEnable(self, limit):  # show or hide the horizontal signal limit reminders
        if limit.isChecked():
            self.hline.show()
        else:
            self.hline.hide()

    def tEnable(self):  # show or hide a trace
        if self.guiRef(2).isChecked():
            self.trace.show()
        else:
            self.trace.hide()
        checkboxes.dwm.submit()

    def updateGUI(self, signal):
        self.trace.setData((tinySA.frequencies/1e6), signal)
        if self.markerType != 'Normal' and self.markerType != 'Delta':  # then it must be a peak marker
            self.mPeak(signal)
        if self.vline.value() >= ui.start_freq.value() and self.vline.value() <= ui.stop_freq.value():
            self.vline.label.setText(f'M{self.vline.name()} {tinySA.frequencies[self.fIndex]/1e6:.3f}MHz  {signal[self.fIndex]:.1f}dBm')
        if not tinySA.sweeping:  # measurement thread is stopping
            ui.scan_button.setText('Stopping ...')
            ui.scan_button.setStyleSheet('background-color: orange')
            ui.run3D.setText('Stopping ...')
            ui.run3D.setStyleSheet('background-color: orange')


class WorkerSignals(QObject):
    error = pyqtSignal(str)
    result = pyqtSignal(np.ndarray)
    result3D = pyqtSignal(np.ndarray)
    finished = pyqtSignal()


class Worker(QRunnable):
    '''Worker threads so that functions can run outside GUI event loop'''

    def __init__(self, fn, *args):
        super(Worker, self).__init__()
        self.fn = fn
        self.args = args
        self.signals = WorkerSignals()

    @pyqtSlot()
    def run(self):
        '''Initialise the runner'''
        logging.info(f'{self.fn.__name__} thread running')
        self.fn(*self.args)
        logging.info(f'{self.fn.__name__} thread stopped')


class database():
    '''configuration data are stored in a SQLite database'''

    def __init__(self):
        self.db = None

    def connect(self):
        self.db = QSqlDatabase.addDatabase('QSQLITE')
        if QtCore.QFile.exists(os.path.join(basedir, 'QtTSAprefs.db')):
            self.db.setDatabaseName(os.path.join(basedir, 'QtTSAprefs.db'))
            self.db.open()
            logging.info(f'Database open: {self.db.isOpen()}')
            self.db.exec('PRAGMA foreign_keys = ON')
        else:
            logging.info('Database file missing')
            popUp('Database file missing', 'OK', QMessageBox.Critical)

    def disconnect(self):
        # xyz.tm.submitAll()
        self.db.close()
        logging.info(f'Database open: {self.db.isOpen()}')
        QSqlDatabase.removeDatabase(QSqlDatabase.database().connectionName())


class modelView():
    '''set up and process data models bound to the GUI widgets'''

    def __init__(self, tableName):
        self.tableName = tableName
        self.tm = QSqlRelationalTableModel()
        self.dwm = QDataWidgetMapper()
        self.currentRow = 0

    def createTableModel(self):
        # add exception handling?
        self.tm.setTable(self.tableName)
        self.dwm.setModel(self.tm)
        self.dwm.setSubmitPolicy(QDataWidgetMapper.AutoSubmit)

    def addRow(self):  # adds a blank row to the frequency bands table widget
        self.tm.insertRow(self.currentRow + 1)
        self.currentRow += 1
        preferences.freqBands.selectRow(self.currentRow)

    def saveChanges(self):
        self.dwm.submit()

    def deleteRow(self):  # deletes row selected by the up/down arrows on the frequency bands table widget
        self.tm.removeRow(self.currentRow)

    def upRow(self):
        if self.currentRow > 0:
            self.currentRow -= 1
            preferences.freqBands.selectRow(self.currentRow)
        else:
            return

    def downRow(self):
        if self.currentRow < self.tm.rowCount():
            self.currentRow += 1
            preferences.freqBands.selectRow(self.currentRow)
        else:
            return


###############################################################################
# respond to GUI signals

def band_changed():
    index = ui.band_box.currentIndex()
    if index == 0:
        return
    startF = bands.tm.record(index).value('StartF')
    stopF = bands.tm.record(index).value('StopF')
    ui.start_freq.setValue(startF)
    ui.stop_freq.setValue(stopF)
    tinySA.freq_changed(False)


def attenuate_changed():
    atten = ui.atten_box.value()
    if ui.atten_auto.isChecked():
        atten = 'auto'
        ui.atten_box.setEnabled(False)
    else:
        if not ui.lna_box.isChecked():  # attenuator and lna are switched so mutually exclusive
            ui.atten_box.setEnabled(True)
    command = f'attenuate {str(atten)}\r'.encode()
    tinySA.serialSend(command)


def spur_box():
    boxState = ui.spur_box.checkState()
    tinySA.spur(boxState)


def markerToStart():
    S1.mStart()
    S2.mStart()
    S3.mStart()
    S4.mStart()


def markerToCentre():
    S1.mSpread()
    S2.mSpread()
    S3.mSpread()
    S4.mSpread()


def mkr1_moved():
    S1.setDiscrete()
    if S2.markerType != 'Delta' and S3.markerType != 'Delta' and S4.markerType != 'Delta':
        S1.vline.setPen(color='y', width=0.75, style=QtCore.Qt.DashLine)
    else:
        S2.mDelta()
        S3.mDelta()
        S4.mDelta()


def memChanged():
    depth = ui.memSlider.value()
    if depth < ui.avgSlider.value():
        ui.avgSlider.setValue(depth)
    tinySA.scanMemory = depth


def setPreferences():
    checkboxes.dwm.submit()
    numbers.dwm.submit()
    bands.tm.submitAll()
    if tinySA.usb and tinySA.dev:
        if tinySA.tinySA4:  # It's a tinySA Ultra
            bands.tm.setFilter('visible = "1"')
        else:
            bands.tm.setFilter('visible = "1" AND (startF <= 960 AND stopF <= 960)')


def dialogPrefs():
    bands.tm.setFilter('name != "Band"')  # remove filters
    bands.tm.select()
    bands.currentRow = 0
    preferences.freqBands.selectRow(bands.currentRow)
    pwindow.show()


def about():
    message = ('TinySA Ultra GUI programme using Qt5 and PyQt\nAuthor: Ian Jefferson G4IXT\n\nVersion {}'
               .format(app.applicationVersion()))
    popUp(message, 'Ok', QMessageBox.Information)

##############################################################################
# other methods


def activeButtons(tF):
    # disable/enable buttons that send commands to TinySA (Because Comms are in use if scanning)
    ui.atten_auto.setEnabled(tF)
    ui.spur_box.setEnabled(tF)
    ui.lna_box.setEnabled(tF and tinySA.tinySA4)
    ui.rbw_box.setEnabled(tF)
    ui.points_box.setEnabled(tF)
    ui.band_box.setEnabled(tF)
    ui.start_freq.setEnabled(tF)
    ui.stop_freq.setEnabled(tF)
    ui.centre_freq.setEnabled(tF)
    ui.span_freq.setEnabled(tF)
    ui.memSlider.setEnabled(tF)
    ui.Enabled3D.setEnabled(tF)
    ui.grid.setEnabled(tF)


def exit_handler():
    if tinySA.dev is not None:
        # save the current displayed marker and trace settings as the default
        S1.dSave(1)
        S2.dSave(1)
        S3.dSave(1)
        S4.dSave(1)
        numbers.dwm.submit()
        checkboxes.dwm.submit()
        # stop sweeping
        if tinySA.sweeping:
            tinySA.sweeping = False  # tell the measurement thread to stop
            while tinySA.threadRunning:
                time.sleep(0.1)  # wait for measurements to stop
        tinySA.resume()
        tinySA.closePort()  # close USB connection
    config.disconnect()  # close database
    logging.info('QtTinySA Closed')


def popUp(message, button, icon):
    # icon = QMessageBox.Warning, QMessageBox.Information, QMessageBox.Critical, QMessageBox.Question
    msg = QMessageBox(parent=(window))
    msg.setIcon(icon)
    msg.setText(message)
    msg.addButton(button, QMessageBox.ActionRole)
    msg.exec_()


###############################################################################
# Instantiate classes

tinySA = analyser()

app = QtWidgets.QApplication([])  # create QApplication for the GUI
app.setApplicationName('QtTinySA')
app.setApplicationVersion(' v0.8.0')
window = QtWidgets.QMainWindow()
ui = QtTinySpectrum.Ui_MainWindow()
ui.setupUi(window)

pwindow = QtWidgets.QDialog()  # pwindow is the preferences dialogue box
preferences = QtTSApreferences.Ui_Preferences()
preferences.setupUi(pwindow)

# Traces & markers
S1 = display('1', yellow)
S2 = display('2', magenta)
S3 = display('3', cyan)
S4 = display('4', white)

# Data models for configuration settings
config = database()
config.connect()
bands = modelView('frequencies')
checkboxes = modelView('checkboxes')
numbers = modelView('numbers')
markers = modelView('marker')
traces = modelView('trace')
tracetext = modelView('combo')
markertext = modelView('combo')

###############################################################################
# GUI settings

# pyqtgraph settings for spectrum display
ui.graphWidget.setYRange(-110, 5)
ui.graphWidget.setXRange(87.5, 108)
ui.graphWidget.setBackground('k')  # black
ui.graphWidget.showGrid(x=True, y=True)

ui.graphWidget.setLabel('left', 'Signal', 'dBm')
ui.graphWidget.setLabel('bottom', 'Frequency MHz')

# marker label positions
S1.vline.label.setPosition(0.99)
S2.vline.label.setPosition(0.95)
S3.vline.label.setPosition(0.90)
S4.vline.label.setPosition(0.85)

# signal limit lines
S1.hline.setValue(-25)
S1.hline.label.setText('best')
S2.hline.label.setText('max')
S3.hline.setValue(6)
S3.hline.setPen('red')

###############################################################################
# Connect signals from buttons and sliders.  Connections for freq and rbw boxes are in 'initialise' Fn

ui.scan_button.clicked.connect(tinySA.scan)
ui.run3D.clicked.connect(tinySA.scan)
ui.atten_box.valueChanged.connect(attenuate_changed)
ui.atten_auto.clicked.connect(attenuate_changed)
ui.spur_box.clicked.connect(spur_box)
ui.lna_box.clicked.connect(tinySA.lna)
ui.memSlider.sliderMoved.connect(memChanged)

# marker dragging
S1.vline.sigPositionChanged.connect(mkr1_moved)
S2.vline.sigPositionChanged.connect(S2.setDiscrete)
S3.vline.sigPositionChanged.connect(S3.setDiscrete)
S4.vline.sigPositionChanged.connect(S4.setDiscrete)

# marker setting within span range
ui.mkr_start.clicked.connect(markerToStart)
ui.mkr_centre.clicked.connect(markerToCentre)

# marker checkboxes
ui.marker1.clicked.connect(S1.mEnable)
ui.marker2.clicked.connect(S2.mEnable)
ui.marker3.clicked.connect(S3.mEnable)
ui.marker4.clicked.connect(S4.mEnable)

# marker type changes
ui.m1_type.activated.connect(S1.mType)
ui.m2_type.activated.connect(S2.mType)
ui.m3_type.activated.connect(S3.mType)
ui.m4_type.activated.connect(S4.mType)

# trace checkboxes
ui.trace1.stateChanged.connect(S1.tEnable)
ui.trace2.stateChanged.connect(S2.tEnable)
ui.trace3.stateChanged.connect(S3.tEnable)
ui.trace4.stateChanged.connect(S4.tEnable)

# trace type changes
ui.t1_type.activated.connect(S1.tType)
ui.t2_type.activated.connect(S2.tType)
ui.t3_type.activated.connect(S3.tType)
ui.t4_type.activated.connect(S4.tType)

# 3D graph controls
ui.orbitL.clicked.connect(lambda: tinySA.orbit3D(1, True))
ui.orbitR.clicked.connect(lambda: tinySA.orbit3D(-1, True))
ui.orbitU.clicked.connect(lambda: tinySA.orbit3D(-1, False))
ui.orbitD.clicked.connect(lambda: tinySA.orbit3D(1, False))
ui.timeF.clicked.connect(lambda: tinySA.axes3D(-1, 'X'))
ui.timeR.clicked.connect(lambda: tinySA.axes3D(1, 'X'))
ui.freqR.clicked.connect(lambda: tinySA.axes3D(-1, 'Y'))
ui.freqL.clicked.connect(lambda: tinySA.axes3D(1, 'Y'))
ui.signalUp.clicked.connect(lambda: tinySA.axes3D(-1, 'Z'))
ui.signalDown.clicked.connect(lambda: tinySA.axes3D(1, 'Z'))
ui.gridF.clicked.connect(lambda: tinySA.grid(1))
ui.gridR.clicked.connect(lambda: tinySA.grid(-1))
ui.zoom.sliderMoved.connect(tinySA.zoom3D)
ui.reset3D.clicked.connect(tinySA.reset3D)

# preferences
preferences.neg25Line.stateChanged.connect(lambda: S1.hEnable(preferences.neg25Line))
preferences.zeroLine.stateChanged.connect(lambda: S2.hEnable(preferences.zeroLine))
preferences.plus6Line.stateChanged.connect(lambda: S3.hEnable(preferences.plus6Line))
preferences.addRow.clicked.connect(bands.addRow)
preferences.deleteRow.clicked.connect(bands.deleteRow)
preferences.rowUp.clicked.connect(bands.upRow)
preferences.rowDown.clicked.connect(bands.downRow)
ui.actionPreferences.triggered.connect(dialogPrefs)  # open preferences dialogue when its menu is clicked
ui.actionAbout_QtTinySA.triggered.connect(about)
pwindow.finished.connect(setPreferences)  # update database checkboxes table on dialogue window close


###############################################################################
# set up the application
logging.info(f'{app.applicationName()}{app.applicationVersion()}')

# table models - read/write views of the configuration data
bands.createTableModel()
bands.tm.setSort(2, Qt.AscendingOrder)
bands.tm.setRelation(4, QSqlRelation('boolean', 'ID', 'value'))
bands.tm.setHeaderData(4, Qt.Horizontal, 'Visible')
boolean = QSqlRelationalDelegate(preferences.freqBands)  # set 'view' column true/false to be combo box
preferences.freqBands.setItemDelegate(boolean)
colHeader = preferences.freqBands.horizontalHeader()
colHeader.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)

# populate the bands combobox
ui.band_box.setModel(bands.tm)
ui.band_box.setModelColumn(1)
bands.tm.setFilter('visible = "1"')
bands.tm.select()  # initially select the data in the model

# connect the preferences dialogue box freq band widget to the data model
preferences.freqBands.setModel(bands.tm)
preferences.freqBands.hideColumn(0)  # ID
rowHeader = preferences.freqBands.verticalHeader()
rowHeader.hide()

#  Map database tables to preferences dialogue box fields and to main GUI
#  ** lines need to be in this order and here or the mapping doesn't work **
checkboxes.createTableModel()
checkboxes.dwm.addMapping(preferences.bestPoints, 3)
checkboxes.dwm.addMapping(preferences.neg25Line, 4)
checkboxes.dwm.addMapping(preferences.zeroLine, 5)
checkboxes.dwm.addMapping(preferences.plus6Line, 6)
checkboxes.dwm.addMapping(ui.trace1, 7)
checkboxes.dwm.addMapping(ui.trace2, 8)
checkboxes.dwm.addMapping(ui.trace3, 9)
checkboxes.dwm.addMapping(ui.trace4, 10)
checkboxes.dwm.addMapping(ui.marker1, 11)
checkboxes.dwm.addMapping(ui.marker2, 12)
checkboxes.dwm.addMapping(ui.marker3, 13)
checkboxes.dwm.addMapping(ui.marker4, 14)
checkboxes.dwm.addMapping(ui.lna_box, 15)
checkboxes.dwm.addMapping(ui.points_auto, 16)
checkboxes.tm.select()
checkboxes.dwm.setCurrentIndex(0)  # 0 = (last used) default settings

# The models for saving number, marker and trace settings
numbers.createTableModel()
numbers.dwm.addMapping(preferences.minPoints, 3)
numbers.dwm.addMapping(preferences.maxPoints, 4)
numbers.dwm.addMapping(ui.start_freq, 5)
numbers.dwm.addMapping(ui.stop_freq, 6)
numbers.dwm.addMapping(ui.mPeak, 7)
numbers.tm.select()
numbers.dwm.setCurrentIndex(0)
markers.createTableModel()
traces.createTableModel()
traces.tm.select()

# populate the trace comboboxes
tracetext.createTableModel()
tracetext.tm.setFilter('type = "trace"')
ui.t1_type.setModel(tracetext.tm)
ui.t2_type.setModel(tracetext.tm)
ui.t3_type.setModel(tracetext.tm)
ui.t4_type.setModel(tracetext.tm)
tracetext.tm.select()

# populate the marker comboboxes
markertext.createTableModel()
markertext.tm.setFilter('type = "marker"')
ui.m1_type.setModel(markertext.tm)
ui.m2_type.setModel(markertext.tm)
ui.m3_type.setModel(markertext.tm)
ui.m4_type.setModel(markertext.tm)
markertext.tm.select()

tinySA.set_frequencies()  # set the initial frequency array

# try to open a USB connection to the TinySA hardware
tinySA.openPort()
if tinySA.dev is None:
    tinySA.checkUSB.start(500)  # check again every 500mS

window.show()
window.setWindowTitle(app.applicationName() + app.applicationVersion())
window.setWindowIcon(QtGui.QIcon(os.path.join(basedir, 'tinySAsmall.png')))

###############################################################################
# run the application until the user closes it

try:
    app.exec()
finally:
    exit_handler()  # close cleanly
