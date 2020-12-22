from __future__ import division, print_function
import numpy as np
import matplotlib.pyplot as plt
from mpl_toolkits.axes_grid1 import make_axes_locatable
from astropy.io import fits as pyfits
from matplotlib.backends.backend_pdf import PdfPages
import scipy.optimize as sop
from scipy.signal import resample

from qubicpack.qubicfp import qubicfp
import qubic.fibtools as ft
from qubic import selfcal_lib as scal

__all__ = ['Fringes_Analysis']


# ============== Get data ==============
def get_data(datafolder, asics, src_data=False, subtract_t0=True):
    """
    Get the TODs for one ASIC.
    Parameters
    ----------
    datafolder: str
        Folder containing the data.
    asics: list
        ASIC numbers.
    doplot: bool
    src_data: if True,will return the srouce data as well
    subtract_t0: if True, will remove to the time the first time element

    Returns
    -------
    Time and signal for all TES in one ASIC.
    If src_data is True: will also return the  source time and signal
    """

    # Qubicpack object
    a = qubicfp()
    a.verbosity = 0
    a.read_qubicstudio_dataset(datafolder)

    # TOD from all ASICS
    data = []
    tdata = []
    for i, ASIC in enumerate(asics):
        ASIC = int(ASIC)
        data_oneASIC = a.timeline_array(asic=ASIC)
        data.append(data_oneASIC)
        tdata_oneASIC = a.timeaxis(datatype='science', asic=ASIC)
        if subtract_t0:
            tdata_oneASIC -= tdata_oneASIC[0]
        tdata.append(tdata_oneASIC)
    tdata = np.array(tdata)
    data = np.array(data)

    if src_data:  # Get calibration source data
        tsrc = a.calsource()[0]
        if subtract_t0:
            tsrc -= tsrc[0]
        dsrc = a.calsource()[1]

        return tdata, data, tsrc, dsrc
    else:
        return tdata, data


def plot_TOD(tdata, data, TES, tsrc=None, dsrc=None, xlim=None, figsize=(12, 6)):
    plt.figure(figsize=figsize)
    ax = plt.gca()
    ax.plot(tdata, data[TES - 1, :])
    ax.set_title(f'TOD for TES {TES}')
    if xlim is not None:
        ax.set_xlim(0, xlim)
    plt.show()

    if dsrc is not None:
        plt.figure(figsize=figsize)
        ax = plt.gca()
        ax.plot(tsrc, dsrc[TES - 1, :])
        ax.set_title('Calibration source data')
        if xlim is not None:
            ax.set_xlim(0, xlim)
        plt.show()
    return


def cut_data(t0, tf, t_data, data):
    """
    Cut the TODs from t0 to tf.
    They can be None if you do not want to cut the beginning or the end.
    """
    if t0 is None:
        t0 = t_data[0]
    if tf is None:
        tf = t_data[-1]

    ok = (t_data >= t0) & (t_data <= tf)
    t_data_cut = t_data[ok]
    data_cut = data[:, ok]

    return t_data_cut, data_cut


def cut_data_Nperiods(t0, tf, t_data, data, period):
    """
    Cut the TODs from t0 to tf with an integer number of periods
    They can be None if you do not want to cut the beginning or the end.
    """
    if t0 is None:
        t0 = t_data[0]
    if tf is None:
        tf = t_data[-1]

    nper = np.floor((tf - t0) / period).astype(int)
    tend = t0 + nper * period
    ok = (t_data >= t0) & (t_data <= tend)
    t_data_cut = t_data[ok]
    data_cut = data[ok]

    return t_data_cut, data_cut, nper


def find_right_period(guess, t_data, data_oneTES, delta=1.5, nb=250):
    ppp = np.linspace(guess - delta, guess + delta, nb)
    rms = np.zeros(len(ppp))
    for i in range(len(ppp)):
        xin = t_data % ppp[i]
        yin = data_oneTES
        xx, yy, dx, dy, o = ft.profile(xin, yin, nbins=100, plot=False)
        rms[i] = np.std(yy)
    period = ppp[np.argmax(rms)]

    return ppp, rms, period


def make_diff_sig(params, t, wt, data):
    """
    Make the difference between the TODs and the simulation.
    Parameters
    ----------
    params: list
        ctime, starting time, the 6 amplitudes.
    t : array
        Time sampling.
    wt: float
        Waiting time [s], number of second the signal keep constant.
    data: array with TODs

    """

    thesim = ft.simsig_fringes(t, wt, params)
    diff = data - thesim
    return diff


def make_combination(param_est, verbose=0):
    """ Make the combination to get the fringes:
        S_tot - Cminus_i - Cminus_j + Sminus_ij using the amplitudes found in the fit."""
    amps = param_est[2:8]
    if verbose > 0:
        print('Check:', amps[2], amps[4])
    return (amps[0] + amps[3] + amps[5]) / 3 + amps[2] - amps[1] - amps[4]


def weighted_sum(vals, errs, coeffs):
    thesum = np.sum(coeffs * vals)
    thesigma = np.sqrt(np.sum(coeffs ** 2 * errs ** 2))
    return thesum, thesigma


def analyse_fringesLouise(datafolder, asics, t0=None, tf=None, wt=5.,
                          lowcut=1e-5, highcut=2., nbins=120,
                          notch=np.array([[1.724, 0.005, 10]]),
                          tes_check=28, param_guess=[0.1, 0., 1, 1, 1, 1, 1, 1],
                          median=False, verbose=True, ):
    """
    Parameters
    ----------
    datafolder: str
        Folder containing the data.
    t0, tf: float
        Start and end time to cut the TODs.
    wt: float
        Waiting time [s] on each step.
    lowcut, highcut: float
        Low and high cut for filtering
    nbins: int
        Number of bins for filtering
    notch: array
        Defined a notch filter.
    tes_check: int
        One TES to check the period.
    param_guess: list
        ctime, starting time, the 6 amplitudes.
    median: bool
        Parameter for folding.
    read_data: array
        If it is None, it will read the data,
        else, it will use the one you pass here (saves time).
    verbose: bool
    Returns
    -------
    Time, folded signal, the 8 parameters estimated with the fit,
    the combination of the amplitudes, the period and the residuals
    between the fit and the signal. They are computed for each TES.
    """

    # Read the data
    t_data, data = get_data(datafolder, asics)
    nasics, ndet, _ = data.shape
    ndet_tot = nasics * ndet

    fringes1D = np.zeros(ndet_tot)
    param_est = np.zeros((ndet_tot, 8))
    dfold = np.zeros((ndet_tot, nbins))
    residuals_time = np.zeros_like(dfold)

    for i, ASIC in enumerate(asics):
        # Cut the data
        t_data_cut, data_cut = cut_data(t0, tf, t_data[i], data[i])

        # Find the true period
        if i == 0:
            ppp, rms, period = find_right_period(6 * wt, t_data_cut, data_cut[tes_check - 1, :])
            if verbose:
                print('period:', period)
                print('Expected : ', 6 * wt)

        # Fold and filter the data
        fold, tfold, _, _ = ft.fold_data(t_data_cut,
                                         data_cut,
                                         period,
                                         lowcut,
                                         highcut,
                                         nbins,
                                         notch=notch,
                                         median=median,
                                         silent=verbose,
                                         )
        dfold[ndet * i:ndet * (i + 1), :] = fold

        # Fit (Louise method)
        for j in range(ndet):
            index = ndet * i + j
            fit = sop.least_squares(make_diff_sig,
                                    param_guess,
                                    args=(tfold,
                                          period / 6.,
                                          fold[j, :]),
                                    bounds=([0., -2, -2, -2, -2, -2, -2, -2],
                                            [1., 2, 2, 2, 2, 2, 2, 2]),
                                    verbose=verbose
                                    )
            param_est[index, :] = fit.x
            fringes1D[index] = make_combination(param_est[index, :])

            residuals_time[index, :] = dfold[index, :] - ft.simsig_fringes(tfold, period / 6., param_est[index, :])

    return tfold, dfold, param_est, fringes1D, period, residuals_time


def make_w_Michel(t, tm1=12, tm2=2, ph=5):
    # w is made to make the combination to see fringes with Michel's method
    w = np.zeros_like(t)
    wcheck = np.zeros_like(t)
    period = len(w) / 6
    for i in range(len(w)):
        if (((i - ph) % period) >= tm1) and (((i - ph) % period) < period - tm2):
            if (((i - ph) // period) == 0) | (((i - ph) // period) == 3):
                w[i] = 1.
            if (((i - ph) // period) == 1) | (((i - ph) // period) == 2):
                w[i] = -1.

    return w, wcheck


def analyse_fringes_Michel(datafolder, w, t0=None, tf=None, wt=5.,
                           lowcut=0.001, highcut=10, nbins=120,
                           notch=np.array([[1.724, 0.005, 10]]),
                           tes_check=28,
                           verbose=True, median=False, read_data=None, silent=False):
    """
    Compute the fringes with Michel's method.
    """

    res_michel = np.zeros(256)
    folded_bothasics = np.zeros((256, nbins))

    for ASIC in [1, 2]:
        if read_data is None:
            # Read the data
            t_data, data = get_data(datafolder, ASIC, doplot=False)
        else:
            t_data, data = read_data[ASIC - 1]

        # Cut the data
        t_data_cut, data_cut = cut_data(t0, tf, t_data, data)

        # Find the true period
        if ASIC == 1:
            ppp, rms, period = find_right_period(6 * wt, t_data_cut, data_cut[tes_check - 1, :])
            if verbose:
                print('period:', period)
                print('Expected : ', 6 * wt)

        # Fold and filter the data
        folded, t, _, newdata = ft.fold_data(t_data_cut,
                                             data_cut,
                                             period,
                                             lowcut,
                                             highcut,
                                             nbins,
                                             notch=notch,
                                             median=median,
                                             silent=silent,
                                             )
        if ASIC == 1:
            folded_bothasics[:128, :] = folded
        else:
            folded_bothasics[128:, :] = folded

        # Michel method
        for TES in range(1, 129):
            index = (TES - 1) + 128 * (ASIC - 1)
            res_michel[index] = np.sum(folded[TES - 1, :] * w)

    return t, folded_bothasics, res_michel, period


def make_keyvals(date, nBLs, Vtes, nstep=6, ecosorb='yes', frame='ONAFP'):
    '''
    Make a dictionary with relevant information on the measurement.
    Assign the FITS keyword values for the primary header
    '''
    keyvals = {}
    keyvals['DATE-OBS'] = (date, 'Date of the measurement')
    keyvals['NBLS'] = (nBLs, 'Number of baselines')
    keyvals['NSTEP'] = (nstep, 'Number of stable steps per cycle')
    keyvals['V_TES'] = (Vtes, 'TES voltage [V]')
    keyvals['ECOSORD'] = (ecosorb, 'Ecosorb on the source')
    keyvals['FRAME'] = (frame, 'Referential frame for (X, Y) TES')

    return keyvals


def make_fdict(allBLs, allwt, allNcycles, xTES, yTES, t,
               allfolded, allparams, allfringes1D, allperiods, allresiduals):
    """ Make a dictionary with all relevant data."""
    fdict = {}
    fdict['BLS'] = allBLs
    fdict['WT'] = allwt
    fdict['NCYCLES'] = allNcycles
    fdict['X_TES'] = xTES
    fdict['Y_TES'] = yTES
    fdict['TIME'] = t
    fdict['FOLDED'] = allfolded
    fdict['PARAMS'] = allparams
    fdict['FRINGES_1D'] = allfringes1D
    fdict['PERIODS'] = allperiods
    fdict['RESIDUALS'] = allresiduals

    return fdict


def write_fits_fringes(out_dir, save_name, keyvals, fdict):
    """ Save a .fits with the fringes data."""
    if out_dir[-1] != '/':
        out_dir += '/'

    # Header creation
    hdr = pyfits.Header()
    for key in keyvals.keys():
        hdr[key] = (keyvals[key])

    hdu_prim = pyfits.PrimaryHDU(header=hdr)
    allhdu = [hdu_prim]
    for key in fdict.keys():
        hdu = pyfits.ImageHDU(data=fdict[key], name=key)
        allhdu.append(hdu)

    thdulist = pyfits.HDUList(allhdu)
    thdulist.writeto(out_dir + save_name, 'warn')

    return


def read_fits_fringes(file):
    """
    Read a .fits where you saved the data and returns two dictionaries with
    the header content and the data themselves.
    """
    hdulist = pyfits.open(file)
    header = hdulist[0].header
    print(header.keys)

    fringes_dict = {}
    for i in range(1, len(hdulist)):
        extname = hdulist[i].header['EXTNAME']
        data = hdulist[i].data
        fringes_dict[extname] = data

    return header, fringes_dict


def make_mask2D_thermometers_TD():
    mask_thermos = np.ones((17, 17))
    mask_thermos[0, 12:] = np.nan
    mask_thermos[1:5, 16] = np.nan
    return mask_thermos


def remove_thermometers(x, y, combi):
    """Remove the 8 thermometers.
    Returns AD arrays with 248 values and not 256."""
    combi = combi[x != 0.]
    x = x[x != 0.]
    y = y[y != 0.]
    return x, y, combi


# def plot_fringes_onFP(q, BL_index, keyvals, fdict, mask=None, lim=2, cmap='bwr', cbar=True, s=None):
#     """Plot fringes on the FP with imshow and with a scatter plot."""
#     if type(keyvals['NSTEP']) is tuple:
#         for i in keyvals:
#             keyvals[i] = keyvals[i][0]
#
#     BL = fdict['BLS'][BL_index]
#     date = keyvals['DATE-OBS']
#     x = fdict['X_TES']
#     y = fdict['Y_TES']
#     fringes1D = fdict['FRINGES_1D'][BL_index]
#     frame = keyvals['FRAME']
#
#     x, y, fringes1D = remove_thermometers(x, y, fringes1D)
#
#     if mask is None:
#         mask = make_mask2D_thermometers_TD()
#
#     fig = plt.figure()
#     fig.suptitle(f'Baseline {BL} - ' + date, fontsize=14)
#     ax0 = plt.subplot(121)
#     img = ax0.imshow(np.nan_to_num(fringes2D * mask),
#                        vmin=-lim, vmax=lim,
#                        cmap=cmap,
#                        interpolation='Gaussian')
#     ft.qgrid()
#     ax0.set_title('Imshow', fontsize=14)
#     if cbar:
#         divider = make_axes_locatable(ax0)
#         cax = divider.append_axes('right', size='5%', pad=0.05)
#         fig.colorbar(img, cax=cax)
#
#     ax1 = plt.subplot(122)
#     scal.scatter_plot_FP(q, x, y, fringes1D, frame,
#                          fig=fig, ax=ax1,
#                          s=s,
#                          title='Scatter plot',
#                          unit=None,
#                          cmap=cmap,
#                          vmin=-lim, vmax=lim,
#                          cbar=cbar
#                          )
#     return

#
# def save_fringes_pdf_plots(out_dir, q, keyvals, fdict, mask=None, **kwargs):
#     """Save all the fringe plots (all baselines) in a pdf file."""
#     if type(keyvals['NSTEP']) is tuple:
#         for i in keyvals:
#             keyvals[i] = keyvals[i][0]
#
#     neq = keyvals['NBLS']
#     date = keyvals['DATE-OBS']
#     myname = 'Fringes_' + date + f'_{neq}BLs.pdf'
#
#     with PdfPages(out_dir + myname) as pp:
#         for i in range(neq):
#             plot_fringes_onFP(q, i, keyvals, fdict, mask=mask, **kwargs)
#             pp.savefig()
#     return


def plot_folded_fit(TES, BL_index, keyvals, fdict, ax=None, legend=True):
    """Plot one folded signal for one TES with the fit and the residuals."""
    if type(keyvals['NSTEP']) is tuple:
        for i in keyvals:
            keyvals[i] = keyvals[i][0]

    params = fdict['PARAMS'][BL_index][TES - 1, :]  # Fit parameters
    t0 = params[1]  # Starting time
    amps = params[2:8]  # Amplitudes
    t = fdict['TIME']  # Time
    folded = fdict['FOLDED'][BL_index][TES - 1, :]  # Folded signal
    period = fdict['PERIODS'][BL_index]  # Period
    nstep = keyvals['NSTEP']
    stable_time = period / nstep
    resid = fdict['RESIDUALS'][BL_index][TES - 1, :]  # Residuals

    # Plot
    if ax is None:
        fig, ax = plt.subplots()
    ax.plot(t, folded, label='folded signal')
    ax.plot(t, ft.simsig_fringes(t, stable_time, params), label='Fit')
    ax.plot(np.arange(0, period, stable_time) + t0, amps, 'ro', label='Amplitudes')
    ax.plot(t, resid, label='Residuals: RMS={0:6.4f}'.format(np.std(resid)))

    for k in range(nstep):
        ax.axvline(x=stable_time * k + t0, color='k', ls=':', alpha=0.3)
    ax.set_title(f'TES {TES}', fontsize=14)
    ax.set_ylim(-2.5, 2.5)
    if legend:
        ax.legend(loc='upper right')
    return


def save_folded_fit_pdf_plots(out_dir, keyvals, fdict):
    """Save all the plots (folded signal, fit and residuals)
    for all TES in a .pdf."""
    if type(keyvals['NSTEP']) is tuple:
        for i in keyvals:
            keyvals[i] = keyvals[i][0]

    nBLs = keyvals['NBLS']
    date = keyvals['DATE-OBS']
    myname = 'Folded_fit_' + date + f'_{nBLs}BLs.pdf'

    with PdfPages(out_dir + myname) as pp:
        plt.figure()
        plt.text(-1, 0, f'Data from {date}', fontsize=40)
        plt.xlim(-2, 2)
        plt.ylim(-2, 2)
        plt.axis('off')
        pp.savefig()
        for BL_index in range(nBLs):
            BL = fdict['BLS'][BL_index]
            plt.figure()
            plt.text(-1, 0, f'Baseline {BL}', fontsize=40)
            plt.xlim(-2, 2)
            plt.ylim(-2, 2)
            plt.axis('off')
            pp.savefig()
            for page in range(11):
                fig, axs = plt.subplots(6, 4, figsize=(15, 25))
                axs = np.ravel(axs)
                for t in range(24):
                    ax = axs[t]
                    TES = page * 24 + t
                    if TES < 256:
                        plot_folded_fit(TES, BL_index, keyvals, fdict, ax=ax, legend=False)
                pp.savefig()
    return


def plot_sum_diff_fringes(q, keyvals, fdict, mask=None, lim=2, cmap='bwr'):
    """Plot the sum and the difference of all equivalent baselines."""
    if type(keyvals['NSTEP']) is tuple:
        for i in keyvals:
            keyvals[i] = keyvals[i][0]

    fringes2D = fdict['FRINGES_2D']
    allBLs = fdict['BLS']
    date = keyvals['DATE-OBS']

    if mask is None:
        mask = make_mask2D_thermometers_TD()

    BLs_sort, BLs_type = scal.find_equivalent_baselines(allBLs, q)
    ntype = np.max(BLs_type) + 1  # Number of equivalency types

    for j in range(ntype):
        images = np.array(fringes2D)[BLs_type == j]
        neq = len(BLs_sort[j])  # Number of equivalent baselines for that type
        sgns = np.ones((neq, 17, 17))
        for i in range(neq):
            sgns[i, :, :] *= (-1) ** i

        av_fringe = np.sum(images, axis=0) / neq
        diff_fringe = np.sum(images * sgns, axis=0) / neq

        plt.subplots(1, 2)
        plt.suptitle(f'{neq} BLs - {date}', fontsize=14)

        plt.subplot(121)
        plt.imshow(np.nan_to_num(av_fringe * mask),
                   vmin=-lim, vmax=lim,
                   cmap=cmap,
                   interpolation='Gaussian')
        ft.qgrid()
        plt.title(f'Imshow - Sum / {neq}', fontsize=14)
        plt.colorbar()

        plt.subplot(122)
        plt.imshow(np.nan_to_num(diff_fringe * mask),
                   vmin=-lim, vmax=lim,
                   cmap=cmap,
                   interpolation='Gaussian')
        ft.qgrid()
        plt.title(f'Imshow - Diff / {neq}', fontsize=14)
        plt.colorbar()
        plt.tight_layout()

    return


def plot_fringes_scatter(q, xTES, yTES, fringes1D, normalize=True, frame='ONAFP', fig=None, ax=None,
                         cbar=True, lim=1., cmap='bwr', s=None, title='Scatter plot'):
    xTES, yTES, fringes1D = remove_thermometers(xTES, yTES, fringes1D)

    if normalize:
        fringes1D /= np.nanstd(fringes1D)

    if ax is None:
        fig, ax = plt.subplots()
    scal.scatter_plot_FP(q, xTES, yTES, fringes1D, frame,
                         fig=fig, ax=ax,
                         s=s,
                         title=title,
                         unit=None,
                         cmap=cmap,
                         vmin=-lim, vmax=lim,
                         cbar=cbar
                         )
    return


def plot_fringes_imshow_interp(fringes1D, normalize=True, interp='Gaussian', mask=None,
                               fig=None, ax=None, cbar=True, lim=1., cmap='bwr', title='Imshow'):
    # Make the 2D fringes
    fringes2D = ft.image_asics(all1=fringes1D)
    if normalize:
        fringes2D /= np.nanstd(fringes2D)

    if mask is None:
        mask = make_mask2D_thermometers_TD()

    if ax is None:
        fig, ax = plt.subplots()
    img = ax.imshow(np.nan_to_num(fringes2D * mask),
                    vmin=-lim, vmax=lim,
                    cmap=cmap,
                    interpolation=interp)
    ft.qgrid()
    ax.set_title(title, fontsize=14)
    if cbar:
        divider = make_axes_locatable(ax)
        cax = divider.append_axes('right', size='5%', pad=0.05)
        fig.colorbar(img, cax=cax)
    return


def plot_folding(tfold, datafold, period, nper, skip_rise, skip_fall, suptitle=None, figsize=(12, 6)):
    fig, axs = plt.subplots(1, 2, figsize=figsize)
    fig.suptitle(suptitle)
    ax1, ax2 = np.ravel(axs)

    ax1.imshow(datafold,
               origin='lower',
               aspect='auto',
               extent=[0, np.max(tfold) + (tfold[1] - tfold[0]) / 2, 0, nper + 0.5])
    for i in range(6):
        ax1.axvline(x=i * (period / 6), color='k', lw=3)
    ax1.set_xlabel('Time in period')
    ax1.set_ylabel('Period #')

    for i in range(nper):
        ax2.plot(tfold, datafold[i, :], alpha=0.5)
    for i in range(6):
        ax2.axvline(x=i * (period / 6), color='k', lw=3)
        ax2.axvspan(i * (period / 6), (i + skip_rise) * (period / 6), alpha=0.1, color='red')
        ax2.axvspan((i + (1. - skip_fall)) * (period / 6), (i + 1) * (period / 6), alpha=0.1, color='red')

    return


def plot_average_foldedTES(nper, nconfigs, stable_time,
                           vals_per, errs_per,
                           dfold, newdfold, residuals_time,
                           vals, errs, residuals_bin, remove_slope,
                           suptitle=None, figsize=(12, 20)):
    fig, axs = plt.subplots(3, 2, figsize=figsize)
    fig.suptitle(suptitle)
    ax1, ax2, ax3, ax4, ax5, ax6 = np.ravel(axs)

    ttt = np.arange(nconfigs) * stable_time + stable_time / 2  # Mean time of each step

    for i in range(nper):
        if i == 0:
            lab = 'Raw'
        else:
            lab = None
        ax1.errorbar(ttt, vals_per[i, :],
                     yerr=errs_per[i, :],
                     xerr=stable_time / 2, fmt='o', label=lab)
    ax1.set_title('Configuration bins before levelling per period')
    ax1.set_xlabel('Time in period')
    ax1.set_ylabel('Value for each period')
    ax1.legend()

    ax2.plot(np.ravel(dfold), label='Input signal')
    ax2.plot(np.ravel(newdfold), label='Reconstructed')
    ax2.plot(np.ravel(residuals_time), label='Residuals')
    ax2.set_xlabel('time samples')
    ax2.set_ylabel('Time domain signal')
    ax2.set_title('Time domain \n[large drift is actually removed]')
    ax2.legend()

    ax3.plot(np.ravel(vals_per), ls='solid', label='Per Period')
    ax3.plot(np.ravel(vals_per * 0. + vals), ls='solid', label='Values')
    ax3.plot(residuals_bin, ls='solid', label='Residuals')
    ax3.set_xlabel('Time')
    ax3.set_ylabel('Values')
    ax3.set_title('Final Residuals')
    ax3.legend()

    for i in range(nper):
        if i == 0:
            lab = 'remove_slope={}'.format(remove_slope)
        else:
            lab = None
        ax4.errorbar(ttt, vals_per[i, :], yerr=errs_per[i, :],
                     xerr=stable_time / 2, fmt='x', alpha=0.3, color='orange', label=lab)
    ax4.set_title('Final Configurations (after levelling)')
    ax4.set_xlabel('Time in period')
    ax4.set_ylabel('Value')
    ax4.legend()

    ax5.errorbar(ttt, vals, yerr=errs, xerr=stable_time / 2, color='r',
                 label='Final Points', fmt='rx')
    ax5.legend()
    return


def plot_residuals(q, sigres, oktes, xTES, yTES, frame='ONAFP', suptitle=None):
    _, _, sigres = remove_thermometers(xTES, yTES, sigres)
    xTES, yTES, oktes = remove_thermometers(xTES, yTES, oktes)

    mm, ss = ft.meancut(np.log10(sigres), 3)

    fig, axs = plt.subplots(1, 3)
    fig.suptitle(suptitle)
    fig.subplots_adjust(wspace=0.5)
    ax0, ax1, ax2 = axs

    ax0.hist(np.log10(sigres), bins=20, label='{0:5.2f} +/- {1:5.2f}'.format(mm, ss))
    ax0.axvline(x=mm, color='r', ls='-', label='Mean')
    ax0.axvline(x=mm - ss, color='r', ls='--', label='1 sigma')
    ax0.axvline(x=mm + ss, color='r', ls='--')
    ax0.axvline(x=mm - 2 * ss, color='r', ls=':', label='2 sigma')
    ax0.axvline(x=mm + 2 * ss, color='r', ls=':')
    ax0.set_xlabel('np.log10(TOD Residuals)')
    ax0.set_title('Histogram residuals')
    ax0.legend()

    scal.scatter_plot_FP(q, xTES, yTES, oktes, frame=frame,
                         fig=fig, ax=ax1, cmap='bwr', cbar=False, s=60, title='TES OK (2sig)')

    scal.scatter_plot_FP(q, xTES, yTES, sigres * oktes, frame=frame,
                         fig=fig, ax=ax2, cmap='bwr', cbar=True, unit=None, s=60, title='TOD Residuals')
    return


def plot_fringes_errors(q, fringes1D, err_fringes1D, xTES, yTES, frame='ONAFP', suptitle=None):
    _, _, fringes1D = remove_thermometers(xTES, yTES, fringes1D)
    xTES, yTES, err_fringes1D = remove_thermometers(xTES, yTES, err_fringes1D)

    mm, ss = ft.meancut(fringes1D, 3)
    rng = 3 * ss

    fig, axs = plt.subplots(1, 2)
    fig.suptitle(suptitle)
    fig.subplots_adjust(wspace=0.5)
    ax0, ax1 = axs
    scal.scatter_plot_FP(q, xTES, yTES, err_fringes1D, frame=frame,
                         fig=fig, ax=ax0, cmap='bwr', cbar=True, unit=None, s=80, title='Errors',
                         vmin=-rng, vmax=rng)

    scal.scatter_plot_FP(q, xTES, yTES, np.abs(fringes1D / err_fringes1D), frame=frame,
                         fig=fig, ax=ax1, cmap='bwr', cbar=True, unit=None, s=80, title='|Values / Errors|',
                         vmin=0, vmax=3)
    return


def reorder_data(data, xdata, ydata, xqsoft, yqsoft):
    ndata = len(data)
    ndet = xdata.shape[0]
    data_ordered = []
    for k in range(ndata):
        olddata = data[k]
        newdata = np.zeros_like(olddata)
        for det in range(ndet):
            index_simu = np.where((xqsoft == xdata[det]) & (yqsoft == ydata[det]))[0][0]
            newdata[index_simu] = olddata[det]
        data_ordered.append(newdata)
    return data_ordered


def find_t0(tfold, dfold, period, nconfigs=6, doplot=False):
    """
    Find time where configuration change in the square modulation.
    """

    # Average the signal over all periods
    msignal = np.mean(dfold, axis=0)
    # calculate the derivative and find where it is high
    dsignal = np.abs(np.gradient(msignal))
    md, sd = ft.meancut(dsignal, 3)
    thr = np.abs(dsignal - md) > (3 * sd)

    # Let's find clusters of high derivatives:
    # each time we take the first high derivative element
    t_change = tfold[thr]
    expected_stable_time = period / nconfigs
    start_times = []
    incluster = 0
    for i in range(len(t_change)):
        if incluster == 0:
            start_times.append(t_change[i])
            incluster = 1
        if i > 0:
            if (t_change[i] - t_change[i - 1]) > (expected_stable_time * 0.6):
                incluster = 0
    start_times = np.array(start_times)

    # Now we take the median of all start_times modulo period/nconfigs
    t0 = np.median(start_times % (period / nconfigs))

    if doplot:
        plt.figure(figsize=(10, 8))
        plt.plot(tfold, msignal, label='Mean over periods')
        plt.plot(tfold, dsignal, label='Derivative')
        plt.plot(tfold[thr], dsignal[thr], 'ro', label='High Derivative (>3sig)')
        for i in range(len(start_times)):
            if i == 0:
                lab = 'Found Start times'
            else:
                lab = None
            plt.axvline(x=start_times[i], ls='--', label=lab, alpha=0.5)
        for i in range(6):
            if i == 0:
                lab = 'Median Start Time (modulo period/6)'
            else:
                lab = None
            plt.axvline(x=t0 + i * period / nconfigs, color='r', ls='--', label=lab)
        plt.legend(framealpha=0.2)
        plt.title('t0 determination on Reference TES')
        plt.xlabel('Time in Period')
        plt.ylabel('Signal averaged over periods')
        plt.tight_layout()

    return t0


def average_datafold_oneTES(tfold, dfold, period, skip_rise=0., skip_fall=0.,
                            median=True, remove_slope=False,
                            all_h=[True, False, False, True, False, True], speak=False,
                            doplot=False):
    """
    Calculating the average in each bin over periods in various ways:
        1/ We can use the whole flat section or cut a bit at the beginning and at the end
        2/ Simple average
        3/ more fancy stuff: removing a slope determined by asking the 3 measurements of "all horns" to be equal
    """

    # We assume that the array has been np.rolled so that the t0 is in time sample 0
    nper, nsp_per = np.shape(dfold)
    nconfigs = len(all_h)
    stable_time = period / nconfigs

    status = np.zeros(nconfigs)

    # Remove the average of each period
    dfold = (dfold.T - np.mean(dfold, axis=1)).T

    # Perform first an average/median in each of the stable sections of each period
    # (possibly skipping beginning and end)
    vals_per = np.zeros((nper, nconfigs))
    errs_per = np.zeros((nper, nconfigs))
    for i in range(nconfigs):
        # Cut the data
        tstart = i * stable_time + skip_rise * stable_time
        tend = (i + 1) * stable_time - skip_fall * stable_time
        ok = (tfold >= tstart) & (tfold < tend)
        for j in range(nper):
            if median:
                vals_per[j, i] = np.median(dfold[j, ok])
            else:
                vals_per[j, i], _ = ft.meancut(dfold[j, ok], 3)
            errs_per[j, i] = np.std(dfold[j, ok])

    if remove_slope:
        # Fit a slope between the "all horns open" configurations and remove it
        xx = np.arange(6)
        for i in range(nper):
            pars, cc = np.polyfit(np.arange(6)[all_h], vals_per[i, all_h], 1, w=1. / errs_per[i, all_h] ** 2, cov=True)
            errfit = np.sqrt(np.diag(cc))
            vals_per[i, :] = vals_per[i, :] - (pars[0] * xx + pars[1])
    else:
        # Remove the average off "all horns open configurations"
        for i in range(nper):
            vals_per[i, :] -= np.mean(vals_per[i, all_h])

    # Average/median all periods
    vals = np.zeros(nconfigs)
    errs = np.zeros(nconfigs)
    for i in range(nconfigs):
        if median:
            vals[i] = np.median(vals_per[:, i])
        else:
            vals[i] = np.mean(vals_per[:, i])
        errs[i] = np.std(vals_per[:, i])
        # Try to detect cases where switches did not work properly
        if errs[i] > (4 * np.mean(errs_per[:, i])):
            status[i] += 1

    # Residuals in time domain (not too relevant as some baselines were removed
    # as a result, large fluctuations in time-domain are usually well removed)
    newdfold = np.zeros_like(dfold)
    for i in range(nconfigs):
        newdfold[:, i * nsp_per // 6:(i + 1) * nsp_per // 6] = vals[i]
    residuals_time = dfold - newdfold

    # We would rather calculate the relevant residuals in the binned domain
    # between the final values and those after levelling
    residuals_bin = np.ravel(vals_per - vals)
    _, sigres = ft.meancut(residuals_bin, 3)

    if speak:
        for i in range(nconfigs):
            print('############')
            print('config {}'.format(i))
            for j in range(nper):
                print('per {}: {} +/- {}'.format(j, vals_per[j, i], errs_per[j, i]))
            print('============')
            print('Value {} +/- {}'.format(vals[i], errs[i]))
            print('============')

    if doplot:
        plot_average_foldedTES(nper, nconfigs, stable_time,
                               vals_per, errs_per,
                               dfold, newdfold, residuals_time,
                               vals, errs, residuals_bin, remove_slope)

    return vals, errs, residuals_time, residuals_bin, sigres, status


def folding_oneTES(timeTES, dataTES, period, t0,
                   lowcut=1e-5, highcut=5., notch=np.array([[1.724, 0.005, 30]]),
                   nsp_per=240, all_h=[True, False, False, True, False, True],
                   skip_rise=0.2, skip_fall=0.1, doplot=True):
    nconfigs = len(all_h)

    # First Step: Data Filtering
    dfilter = ft.filter_data(timeTES, dataTES, lowcut, highcut, notch=notch, rebin=True)

    # Crop the data in order to have an integer number of periods
    tcrop, dcrop, nper = cut_data_Nperiods(None, None, timeTES, dfilter, period)

    # Resample the signal
    newtime = np.linspace(tcrop[0], tcrop[-1], nper * nsp_per)
    newdata = resample(dcrop, nper * nsp_per)
    if doplot:
        plt.figure(figsize=(8, 6))
        plt.plot(newtime, newdata)
        plt.xlabel('Time')
        plt.ylabel('ADU')

    # Fold the data
    tfold = np.linspace(0, period, nsp_per)
    dfold = np.reshape(newdata, (nper, nsp_per))

    # Shift the folded data in order to have t0=0
    droll = np.roll(dfold, -int(t0 / period * nsp_per), axis=1)

    # Roughly remove the average of the all_h configurations
    ok_all_horns = np.zeros_like(tfold, dtype=bool)
    for i in range(nconfigs):
        if all_h[i]:
            tmini = i * period / nconfigs + skip_rise * period / nconfigs
            tmaxi = (i + 1) * period / nconfigs - skip_fall * period / nconfigs
            ok = (tfold >= tmini) & (tfold < tmaxi)
            ok_all_horns[ok] = True
    droll -= np.median(droll[:, ok_all_horns])

    if doplot:
        plot_folding(tfold, droll, period, nper, skip_rise, skip_fall)

    return tfold, droll


def analyse_fringesJC(directory, asics=[1, 2],
                      lowcut=1e-5, highcut=5., notch=np.array([[1.724, 0.005, 30]]),
                      refTESnum=95, refASICnum=1, expected_period=30,
                      all_h=[True, False, False, True, False, True],
                      nsp_per=240, skip_rise=0.2, skip_fall=0.1, remove_slope=True,
                      force_period=None, force_t0=None,
                      verbose=True, doplot=True):
    tdata, data = get_data(directory, asics)
    print(tdata.shape, data.shape)
    nasics, ndet, _ = data.shape
    nconfigs = len(all_h)

    # ================= Determine the correct period reference TES ========
    if force_period is None:
        # Filter
        dfilter = ft.filter_data(tdata[refASICnum - 1, :],
                                 data[refASICnum - 1, refTESnum, :],
                                 lowcut, highcut, notch=notch, rebin=True)
        ppp, rms, period = find_right_period(expected_period,
                                             tdata[refASICnum - 1, :],
                                             dfilter,
                                             delta=0.5, nb=100)
        if verbose:
            print('Found period {0:5.3f}s on TES#{1:}'.format(period, refTESnum))
    else:
        period = force_period
        if verbose:
            print('Using Forced period {0:5.3f}s'.format(period))

    # =============== Determine t0 on reference TES ======================
    if force_t0 is None:
        # Filter, crop, resample and fold
        dfilter = ft.filter_data(tdata[refASICnum - 1, :],
                                 data[refASICnum - 1, refTESnum, :],
                                 lowcut, highcut, notch=notch, rebin=True)
        tcrop, dcrop, nper = cut_data_Nperiods(None, None,
                                               tdata[refASICnum - 1, :],
                                               dfilter, period)
        newdata = resample(dcrop, nper * nsp_per)
        tfold = np.linspace(0, period, nsp_per)
        dfold = np.reshape(newdata, (nper, nsp_per))
        t0 = find_t0(tfold, dfold, period, doplot=doplot)
        if verbose:
            print('Found t0 {0:5.3f}s on TES#{1:}'.format(t0, refTESnum))
    else:
        t0 = force_t0
        if verbose:
            print('Using forced t0 {0:5.3f}s'.format(t0))

    # =============== Loop on ASICs and TES ======================
    vals = np.zeros((nasics * ndet, nconfigs))
    errs = np.zeros((nasics * ndet, nconfigs))
    sigres = np.zeros((nasics * ndet))
    status = np.zeros((nasics * ndet, nconfigs))
    fringes1D = np.zeros((nasics * ndet))
    err_fringes1D = np.zeros((nasics * ndet))
    coeffs = np.array([1. / 3, -1, 1, 1. / 3, -1, 1. / 3])
    for i, ASIC in enumerate(asics):
        print(f'*********** Starting ASIC{ASIC} **************')
        for j, TES in enumerate(np.arange(1, 129)):

            index = i * ndet + j
            print(index)
            if (i == (refASICnum - 1)) & (j == (refTESnum - 1)):
                speak = True
                thedoplot = True * doplot
            else:
                speak = False
                thedoplot = False

            timeTES = tdata[i, :]
            dataTES = data[i, j, :]
            tfold, droll = folding_oneTES(timeTES, dataTES, period, t0,
                                          lowcut=lowcut, highcut=highcut, notch=notch,
                                          nsp_per=nsp_per, all_h=all_h,
                                          skip_rise=skip_rise, skip_fall=skip_fall,
                                          doplot=thedoplot)

            # Calculate the baselines configurations in each TES
            vals[index, :], errs[index, :], res_time, res_bin, sigres[index], status[index,
                                                                              :] = average_datafold_oneTES(tfold,
                                                                                                           droll,
                                                                                                           period,
                                                                                                           all_h=all_h,
                                                                                                           skip_rise=skip_rise,
                                                                                                           skip_fall=skip_fall,
                                                                                                           remove_slope=remove_slope,
                                                                                                           doplot=thedoplot,
                                                                                                           speak=speak)
            if speak:
                print('status:', status[index, :])

            fringes1D[index], err_fringes1D[index] = weighted_sum(vals[index, :], errs[index, :], coeffs)

    # Cut on residuals
    mm, ss = ft.meancut(np.log10(sigres), 3)
    oktes = np.ones((nasics * ndet))
    oktes[np.abs(np.log10(sigres) - mm) > 2 * ss] = np.nan

    return vals, errs, sigres, period, t0, fringes1D, err_fringes1D, oktes, status
