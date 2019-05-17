from __future__ import division
import sys
import time

import healpy as hp
import numpy as np

from pysm.nominal import models

import qubic

import ReadMC
import SpectroImLib as si
import os
from pysimulators import FitsArray

dictfilename = './spectroimaging.dict'

d = qubic.qubicdict.qubicDict()
d.read_from_file(dictfilename)

nf_sub = d['nf_sub']
dirc = './maps/'

try:
	os.makedirs(dirc)
except:
	pass

for  nf in nf_sub:
	sky_config = {'dust': models('d1', d['nside']),'cmb': models('c1', d['nside'])}
	
	d['nf_sub'] = nf
	
	Qubic_sky = si.Qubic_sky(sky_config, d)
	x0 = Qubic_sky.get_simple_sky_map()
	dirc2 = dirc+'nf_sub={}/'.format(nf) 
	try:
		os.makedirs(dirc2)
	except:
		pass
	FitsArray(x0).save(dirc2+'nf_sub={}.fits'.format(nf))

