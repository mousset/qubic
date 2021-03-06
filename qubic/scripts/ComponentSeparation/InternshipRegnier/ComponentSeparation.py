import random
import healpy as hp
import glob
from scipy.optimize import curve_fit
import pickle
from importlib import reload
import time
import scipy
import os
import numpy as np
import matplotlib.pyplot as plt
import sys
import pylab
from pylab import arange, show, cm
from astropy import units as uq
import gc
from qubic import QubicSkySim as qss


### FGBuster functions module
from fgbuster import get_instrument, get_sky, get_observation, ilc, basic_comp_sep, harmonic_ilc, weighted_comp_sep, multi_res_comp_sep  # Predefined instrumental and sky-creation configurations
from fgbuster.visualization import corner_norm, plot_component
from fgbuster.mixingmatrix import MixingMatrix
from fgbuster.observation_helpers import _rj2cmb, _jysr2rj, get_noise_realization

# Imports needed for component separation
from fgbuster import (separation_recipes, xForecast, CMB, Dust, Synchrotron, FreeFree, PowerLaw,  # sky-fitting model
                      basic_comp_sep)  # separation routine
                                           

# Function to get all maps at the same resolution

def same_resol(map1, fwhm, fwhm_target=None, verbose=False) :
    sh = np.shape(map1)
    nb_bands = sh[0]
    delta_fwhm = np.zeros(nb_bands)
    if fwhm_target is None:
        myfwhm = np.max(fwhm)
    else:
        myfwhm = fwhm_target
    print(myfwhm, np.max(fwhm))
    maps_out = np.zeros_like(map1)
    fwhm_out = np.zeros(nb_bands)
    for i in range(map1.shape[0]):
        delta_fwhm_deg = np.sqrt(myfwhm**2-fwhm[i]**2)
        if verbose:
            print('Sub {0:}: Going from {1:5.2f} to {2:5.2f}'.format(i, fwhm[i], myfwhm))
        if delta_fwhm_deg != 0:
            delta_fwhm[i] = delta_fwhm_deg
            print('   -> Convolution with {0:5.2f}'.format(delta_fwhm_deg))
            maps_out[i,:,:] = hp.sphtfunc.smoothing(map1[i,:,:], 
                                                    fwhm=np.radians(delta_fwhm_deg), 
                                                    verbose=False)
        else:
            print('   -> No Convolution'.format(delta_fwhm_deg))
            maps_out[i,:,:] = map1[i,:,:]
            
        fwhm_out[i] = np.sqrt(fwhm[i]**2 + delta_fwhm_deg**2)
    
    return maps_out, fwhm_out, delta_fwhm
                          
class CompSep(object) :

    """
    
    Class that brings together different methods of component separations. Currently, there is only 'fg_buster' definition which work with 2 components (CMB and Dust).
    
    """
    
    def __init__(self, d) :
        
        self.nside = d['nside']
        self.npix = 12 * self.nside**2
        self.Nfin = int(d['nf_sub'])
    
    def fg_buster(self, map1=None, comp=None, freq=None, fwhmdeg=None, target = None, okpix = None) :
        
        """
        --------
        inputs :
            - map1 : map of which we want to separate the components -> array type & (nb_bands, Nstokes, Npix)
            - comp : list type which contains components that we want to separe. Dust must have nu0 in input and we can fixe the temperature.
            - freq : list type of maps frequency
            - fwhmdeg : list type of full width at half maximum (in degrees). It can be different values.
            - target : if target is not None, "same_resol" definition is applied and put all the maps at the same resolution. If target is None, make sure that all the resolution are the same.
            - okpix : boolean array type which exclude the edges of the map. 
        
        --------
        output :
            - r : Dictionary which contains the amplitude of each components, the estimated parameter beta_d and dust temperature. 
        
        """
        
        ins = get_instrument('Qubic' + str(self.Nfin) + 'bands')
        
        
        # Change good frequency and FWHM
        ins.frequency = freq
        ins.fwhm = fwhmdeg
        
        # Change resolution of each map if it's necessary
        map1, tab_fwhm, delta_fwhm = same_resol(map1, fwhmdeg, fwhm_target = target, verbose = True)
        
        # Apply FG Buster
        r = basic_comp_sep(comp, ins, map1[:, :, okpix])
        
        return r
        
    def basic_2_tab(self, X, okpix) :
    
        """
        --------
    	inputs :
    		- X : Dictionnary which come from "fg_buster" definition.
    		- okpix : boolean array type which exclude the edges of the map.
    	
    	--------
    	outputs :
    		- tab_dust and/or tab_cmb : one or two array type which contains differents components. The form of these arrays is (nfreq, nstokes, npix).
    	
    	"""

        X_array = np.zeros((((X.s.shape[0], self.Nfin, 3, self.npix))))
    
        for i in range(X.s.shape[0]) :
            for j in range(self.Nfin) :
                for k in range(3) :
                    X_array[i, j, k, okpix] = X.s[i, k, :]
                
        return X_array
        '''
        if X.s.shape[0] == 1 :
            tab_cmb = np.zeros(((self.Nfin, 3, self.npix)))
        
            for i in range(self.Nfin) :
                for j in range(3) :
                    tab_cmb[i, j, :] = X.s[0, j, :]
           
        
        
        
        elif X.s.shape[0] == 2 :
        	
            tab_cmb = np.zeros(((self.Nfin, 3, self.npix)))
            tab_dust = np.zeros(((self.Nfin, 3, self.npix)))
            
            for i in range(self.Nfin) :
                for j in range(3) :
                    tab_cmb[i, j, okpix] = X.s[0, j, :]
                    tab_dust[i, j, okpix] = X.s[1, j, :]
        
            return tab_cmb, tab_dust
            
        else :
            raise TypeError("Please enter the good number of component")
        '''
        
    def internal_linear_combination(self, map1 = None, comp = None) :
		
        """
        
        ----------
        inputs :
            - nb_bands : number of sub bands
            - map1 : map of which we want to estimate CMB signal

        ----------
        outputs :
            - r : Dictionary for each Stokes parameter (I, Q, U) in a list. To have the amplitude, we can write r[ind_stk].s[0].

        """
		
        ins = get_instrument('Qubic' + str(self.Nfin) + 'bands')
        r = []
        
        # Apply ILC for each stokes parameter
        for i in range(3) :
        	r.append(ilc(comp, ins, map1[:, i, :]))

        return r
    
    def ilc_2_tab(self, X, seenpix) :
    
        tab_cmb = np.zeros(((self.Nfin, 3, self.npix)))
        
        for i in range(3) :
            tab_cmb[0, i, seenpix] = X[i].s[0]
        
        return tab_cmb
                     
                      
                      
                      
                      
                      
                      
                      
                      
                      
                      
                      
                      
                      
                      
                      
                      
                      
                      
                      
                      
                      
                      
                      
