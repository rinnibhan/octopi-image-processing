# -*- coding: utf-8 -*-
"""Octopi Image Processing

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1GyvZFugFX3CaKruUZhvVRZ1lolPW_GWb

## Pipeline
1. Create Folders
2. Crop Images
3. Background removal 
4. Spot detection
5. Process spots
6. Visualize results

- send images (to see diff in bg rem)
- generate random spot images (to fix radius detection)
- connect with git --> then can clone repo anywhere
"""

import imageio
import numpy as np
import matplotlib.pyplot as plt
import cupy as cp
from skimage.color import rgb2gray
import cv2
import cupyx.scipy.ndimage
import scipy.ndimage
from skimage.feature.blob import _prune_blobs
import multiprocessing as mp
import os
import pandas as pd
from cupyx.scipy.ndimage.filters import laplace
from scipy import signal
import time

save_intermediate_steps = True

"""
## I. Create Folders
"""

work_path = '/content/drive/MyDrive/Sophomore/Research/Octopi/Image Processing Code'

os.chdir(work_path)

def create_folders():
  directory = 'cropped'
  if not os.path.exists(directory):
    os.mkdir(directory)

  directory = 'mask'
  if not os.path.exists(directory):
    os.mkdir(directory)

  directory = 'BGremoved'
  if not os.path.exists(directory):
    os.mkdir(directory)

  directory = 'tmp'
  if not os.path.exists(directory):
    os.mkdir(directory)

  directory = 'spotCoordinates_raw'
  if not os.path.exists(directory):
    os.mkdir(directory)

  directory = 'spotCoordinates_final'
  if not os.path.exists(directory):
    os.mkdir(directory)

  directory = 'visualization'
  if not os.path.exists(directory):
    os.mkdir(directory)

  directory = 'spot_data'
  if not os.path.exists(directory):
    os.mkdir(directory)

  directory = 'spot_images'
  if not os.path.exists(directory):
    os.mkdir(directory)

"""## II. Crop Images"""

# cropping parameters
nx = 3280;
ny = 2464;

dx = round(512/8*25/1.12); # 25 um per full step (0.5mm/20)
dy = round(512/8*25/1.12);

cut_left = round( (nx-dx)/2 ) - 150;
cut_right = round( (nx-dx)/2 ) + 150;
cut_up = round( (ny-dy)/2 ) + 0;
cut_down = round( (ny-dy)/2 ) - 0;

nx_cropped = (nx-cut_left-cut_right);
ny_cropped = (ny-cut_up-cut_down);

x_s = cut_left
x_e = nx - cut_right
y_s = cut_down
y_e = ny - cut_up

def rgb2lin(I_sRGB):
  # RGB2LIN Linearize gamma-corrected sRGB or Adobe RGB (1998) values
  # input image should cp array be within the range of [0,1] (use I.astype(float)/255)
	gamma = 2.4
	a     = 1/1.055
	b     = 0.055/1.055
	c     = 1/12.92
	d     = 0.04045

	lin_range = (I_sRGB < d)
	gamma_range = cp.logical_not(lin_range)

	# I_linearRGB = I_sRGB
	I_linearRGB = cp.copy(I_sRGB)
	I_linearRGB[gamma_range] = cp.exp(gamma * cp.log(a * I_sRGB[gamma_range] + b))
	I_linearRGB[lin_range] = c * I_sRGB[lin_range];

	return I_linearRGB

# returns the bf and fluorescence images at (i,j), along with fluorescence mask (in order, as cp arrays)
def process_img(i, j, dir_in = 'data', dir_out = 'cropped', dir_out_mask = 'mask', x_start=x_s, y_start=y_s, x_end=x_e, y_end=y_e):
  # fluorescence 
  filename = '_' + str(i).zfill(4) + '_' + str(j).zfill(4) + '_fluorescent.jpeg'
  print(filename)
  I = cv2.imread(dir_in + '/' + filename)
  I = cp.asarray(I)
  I = I.astype('float')/255
  I = I[y_start:y_end,x_start:x_end,:] # cropping
  I_linRGB = rgb2lin(I) # convert to linearRGB
  cropped_img_f = I_linRGB*255
  mask_img_f = I*255
  if save_intermediate_steps:
    cv2.imwrite(dir_out + '/' + str.replace(filename,'.jpeg','.png'), cp.asnumpy(I_linRGB*255))
    cv2.imwrite(dir_out_mask + '/' + str.replace(filename,'.jpeg','.png'), cp.asnumpy(I*255))

  # brightfield
  filename = '_' + str(i).zfill(4) + '_' + str(j).zfill(4) + '_bf.jpeg'
  I = cv2.imread(dir_in + '/' + filename)
  I = cp.asarray(I)
  I = I.astype('float')/255
  I = I[y_start:y_end,x_start:x_end,:] # cropping
  I_linRGB = rgb2lin(I) # convert to linearRGB
  cropped_img_bf = I_linRGB*255
  if save_intermediate_steps:
    cv2.imwrite(dir_out + '/' + str.replace(filename,'.jpeg','.png'), cp.asnumpy(I_linRGB*255))
  return cropped_img_bf, cropped_img_f, mask_img_f

"""## III. Remove Background"""

def gaussian_kernel(n, std, normalised=True):
    '''
    Generates a n x n matrix with a centered gaussian
    of standard deviation std centered on it. If normalised,
    its volume equals 1.'''
    gaussian1D = signal.gaussian(n, std)
    gaussian2D = np.outer(gaussian1D, gaussian1D)
    if normalised:
        gaussian2D /= (2 * np.pi * (std ** 2))
    return cp.asarray(gaussian2D)

def gaussian_kernel_1d(n, std, normalised=True):
    if normalised:
      return cp.asarray(signal.gaussian(n, std))/(np.sqrt(2 * np.pi)*std)
    return cp.asarray(signal.gaussian(n, std))

# Define parameters

# tophat filters
tophat = cv2.getStructuringElement(2, ksize=(17,17))
tophat_gpu = cp.asarray(tophat)

# 1D Gaussian and Laplace filters
gauss_rs = np.array([4,6,8,10])
gauss_sigmas = np.array([1,1.5,2,2.5])
gauss_ts = np.divide(gauss_rs - 0.5,gauss_sigmas) # truncate value (to get desired radius)
lapl_kernel = cp.array([[0,1,0],[1,-4,1],[0,1,0]])
gauss_filters_1d = []
log_filters = []
for i in range(gauss_rs.shape[0]):
  gauss_filt_1d = gaussian_kernel_1d(gauss_rs[i]*2+1,gauss_sigmas[i],True)
  gauss_filt_1d = gauss_filt_1d.reshape(-1, 1) 
  gauss_filters_1d.append(gauss_filt_1d)

# img_cpu is the rgb image as an np array (s x s x 3)
def rgb_to_g_gpu(img_cpu):
  img_rgb_gpu = cp.asarray(img_cpu)
  return cp.average(img_rgb_gpu, axis=2, weights=cp.array([0.299,0.587,0.114]))

# img_cpu is the CPU BF image
# remove background for brightfield
def rem_bg_bf(img_cpu, i, j, dir_out = 'BGremoved'):
  img_g_gpu = rgb_to_g_gpu(img_cpu)
  img_bth_gpu = 255 - cupyx.scipy.ndimage.black_tophat(img_g_gpu, footprint=tophat_gpu)
  if save_intermediate_steps:
    filename = '_' + str(i).zfill(4) + '_' + str(j).zfill(4) + '_bf.png'
    cv2.imwrite(dir_out + '/' + filename, cp.asnumpy(img_bth_gpu))
  return img_bth_gpu

# img_cpu is the CPU fluorescence image
# remove background for fluorescence
def rem_bg_fl(img_cpu, i, j, dir_out = 'BGremoved'):
  img_g_gpu = cp.asarray(img_cpu)
  img_th_gpu = img_g_gpu
  for k in range(3):
    img_th_gpu[:,:,k] = cupyx.scipy.ndimage.white_tophat(img_g_gpu[:,:,k], footprint=tophat_gpu)
  if save_intermediate_steps:
    filename = '_' + str(i).zfill(4) + '_' + str(j).zfill(4) + '_fluorescent.png'
    cv2.imwrite(dir_out + '/' + filename, cp.asnumpy(img_th_gpu))
  return img_th_gpu

"""## IV. Detect Spots"""

# img_no_bg is the GPU background-removed image
# detect spots
def detect_spots_n(img_no_bg, k, j, thresh=12, dir_out_intermediate='tmp'):
  # apply all filters
  if len(img_no_bg.shape) == 3:
    img_no_bg = cp.average(img_no_bg, axis=2, weights=cp.array([0.299,0.587,0.114]))
  filtered_imgs = []
  for i in range(len(gauss_filters_1d)): # apply LoG filters
    filt_img = cupyx.scipy.ndimage.convolve(img_no_bg, gauss_filters_1d[i])
    filt_img = cupyx.scipy.ndimage.convolve(filt_img, gauss_filters_1d[i].transpose())
    filt_img = cupyx.scipy.ndimage.convolve(filt_img, lapl_kernel)
    filt_img *= -(gauss_sigmas[i]**2)
    filtered_imgs.append(filt_img)
  img_max_proj = cp.max(np.stack(filtered_imgs), axis=0)
  # return img_max_proj
  img_max_filt = cupyx.scipy.ndimage.maximum_filter(img_max_proj, size=3)
  # set pixels < thresh (12) to 0 (so they wont be in img_traceback)
  img_max_filt[img_max_filt < thresh] = 0 # check if uint8
  # origination masks
  img_traceback = cp.zeros(img_max_filt.shape)
  for i in range(len(filtered_imgs)): # trace back pixels to each filtered image
    img_traceback[img_max_filt == filtered_imgs[i]] = i+1
    img_traceback[img_max_filt == 0] = 0 # but make sure all pixels that were 0 are still 0
  ind = np.where(img_traceback != 0)
  spots = np.zeros((ind[0].shape[0],3)) # num spots x 3
  for i in range(ind[0].shape[0]):
    spots[i][0] = int(ind[1][i])
    spots[i][1] = int(ind[0][i])
    spots[i][2] = int(img_traceback[spots[i][1]][spots[i][0]])
  spots = spots.astype(int)
  if save_intermediate_steps:
    filename = '_' + str(k).zfill(4) + '_' + str(j).zfill(4) + '.txt'
    np.savetxt(dir_out_intermediate + '/' + filename, spots, delimiter=" ")
  return spots

# filter spots to avoid overlapping ones
def prune_blobs(spots_list, i, j, dir_out_final='spotCoordinates_raw'):
  overlap = .5
  num_sigma = 4
  min_sigma = 1
  max_sigma = 2.5
  scale = np.linspace(0, 1, num_sigma)[:, np.newaxis]
  sigma_list = scale * (max_sigma - min_sigma) + min_sigma
  # translate final column of lm, which contains the index of the
  # sigma that produced the maximum intensity value, into the sigma
  sigmas_of_peaks = sigma_list[spots_list[:, -1]-1]
  # select one sigma column, keeping dimension
  sigmas_of_peaks = sigmas_of_peaks[:, 0:1] 
  # Remove sigma index and replace with sigmas
  spots_list = np.hstack([spots_list[:,:-1], sigmas_of_peaks])
  result_pruned = _prune_blobs(spots_list, overlap)
  if save_intermediate_steps:
    filename = '_' + str(i).zfill(4) + '_' + str(j).zfill(4) + '.txt'
    np.savetxt(dir_out_final + '/' + filename, result_pruned, delimiter=" ")
  return result_pruned

"""## V. Process Spots"""

def remove_spots_in_maskedRegions(spotList,mask):
	mask = mask.astype('float')/255
	mask = np.sum(mask,axis=-1) # masked out region has pixel value 0 ;# mask[mask>0] = 1 # cv2.imshow('mask',mask) # cv2.waitKey(0)
	for s in spotList:
		x = s[0]
		y = s[1]
		if mask[int(y),int(x)] == 0:
			s[-1] = 0
	spotList_final = np.array([s for s in spotList if s[-1] > 0])
	return spotList_final

def highlightSpots(bgremoved_fluorescence,spotList,contrastBoost=1.6):
	# bgremoved_fluorescence_spotBoxed = np.copy(bgremoved_fluorescence)
	bgremoved_fluorescence_spotBoxed = bgremoved_fluorescence.astype('float')/255 # this copies the image
	bgremoved_fluorescence_spotBoxed = bgremoved_fluorescence_spotBoxed*contrastBoost # enhance contrast
	for s in spotList:
		addBoundingBox(bgremoved_fluorescence_spotBoxed,int(s[0]),int(s[1]),int(s[2]))
	return bgremoved_fluorescence_spotBoxed

def addBoundingBox(I,x,y,r,extension=2,color=[0,0,0.6]):
	ny, nx, nc = I.shape
	x_min = max(x - r - extension,0)
	y_min = max(y - r - extension,0)
	x_max = min(x + r + extension,nx-1)
	y_max = min(y + r + extension,ny-1)
	for i in range(3):
		I[y_min,x_min:x_max+1,i] = color[i]
		I[y_max,x_min:x_max+1,i] = color[i]
		I[y_min:y_max+1,x_min,i] = color[i]
		I[y_min:y_max+1,x_max,i] = color[i]

def extractSpotData(I_bgRemoved,I_raw,spotList,dir_spot_data,FOV_row,FOV_col,extension=1):
  ny, nx, nc = I_bgRemoved.shape
  I_bgRemoved = I_bgRemoved.astype('float')
  columns = ['FOV_row','FOV_col','x','y','r','R','G','B','R_max','G_max','B_max','lap_total','lap_max','numPixels','numSaturatedPixels','idx']
  data_csv_pd = pd.DataFrame(columns=columns)
  i = 0
  for s in spotList:
		# get spot
    x = int(s[0])
    y = int(s[1])
    r = int(s[2])
    x_min = max(x - r - extension,0)
    y_min = max(y - r - extension,0)
    x_max = min(x + r + extension,nx-1)
    y_max = min(y + r + extension,ny-1)
    cropped = I_bgRemoved[y_min:y_max+1,x_min:x_max+1,:]
    cropped_raw = I_raw[y_min:y_max+1,x_min:x_max+1,:]

    # extract spot data
    B = np.sum(cropped[:,:,0])
    G = np.sum(cropped[:,:,1])
    R = np.sum(cropped[:,:,2])
    B_max = np.max(cropped[:,:,0])
    G_max = np.max(cropped[:,:,1])
    R_max = np.max(cropped[:,:,2])
    lap = laplace(np.sum(cropped,2))
    lap_total = np.sum(np.abs(lap))
    lap_max = np.max(np.abs(lap))
    numPixels = cropped[:,:,0].size
    numSaturatedPixels = np.sum(cropped_raw > 254)

    spot_entry = pd.DataFrame.from_dict({'FOV_row':[FOV_row],'FOV_col':[FOV_col],'x':[x],'y':[y],'r':[r],'R':[R],'G':[G],'B':[B],'R_max':[R_max],'G_max':[G_max],'B_max':[B_max],'lap_total':[lap_total],'lap_max':[lap_max],'numPixels':[numPixels],'numSaturatedPixels':[numSaturatedPixels],'idx':[i]})
    data_csv_pd = data_csv_pd.append(spot_entry, ignore_index=True, sort=False)

    i = i + 1

	# save to disk
  fileID = str(FOV_row).zfill(4) + '_' + str(FOV_col).zfill(4)
  data_csv_pd.to_csv(dir_spot_data + '/' + fileID + '.csv',index=False)

def process_spots(i,j,img_no_bg_f,img_mask,img_f,spot_list_purged,dir_spot_data='spot_data',dir_spotList_final = 'spotCoordinates_final', dir_vis = 'visualization'):
  fileID = '_' + str(i).zfill(4) + '_' + str(j).zfill(4)
  # get rid of spots in masked out regions
  spotList_final = remove_spots_in_maskedRegions(spot_list_purged,img_mask)
  if save_intermediate_steps:
    np.savetxt(dir_spotList_final + '/' + fileID + '.txt',spotList_final)
  # highlight spots in background removed fluorescence image
  bgremoved_fluorescence_spotBoxed = highlightSpots(img_no_bg_f,spotList_final)
  cv2.imwrite(dir_vis + '/' + fileID + '.png', cp.asnumpy(bgremoved_fluorescence_spotBoxed*255))
  # extract spot statistics
  extractSpotData(img_no_bg_f,img_f,spotList_final,dir_spot_data,i,j)

"""## Combine All Steps"""

create_folders()

y_start = 0
y_end = 0
x_start = 0
x_end = 1

for i in range(y_start,y_end+1):
  for j in range(x_start,x_end+1):
    time_start = time.time()
    cropped_img_bf, cropped_img_f, mask_img_f = process_img(i, j)
    time_elapsed = time.time() - time_start
    print('cropping images took ' + str(time_elapsed) + ' seconds')

    time_start = time.time()
    img_f_no_bg_gpu = rem_bg_fl(cropped_img_f,i,j)
    time_elapsed = time.time() - time_start
    print('removing fluorescence background took ' + str(time_elapsed) + ' seconds')

    time_start = time.time()
    img_bf_no_bg_gpu = rem_bg_bf(cropped_img_bf,i,j)
    time_elapsed = time.time() - time_start
    print('removing BF background took ' + str(time_elapsed) + ' seconds')

    time_start = time.time()
    init_spots = detect_spots_n(img_f_no_bg_gpu,i,j)
    pruned_spots = prune_blobs(init_spots, i, j)
    time_elapsed = time.time() - time_start
    print('spot detection took ' + str(time_elapsed) + ' seconds')

    time_start = time.time()
    process_spots(i,j,img_f_no_bg_gpu,mask_img_f,cropped_img_f,pruned_spots)
    time_elapsed = time.time() - time_start
    print('processing spots ' + str(time_elapsed) + ' seconds')
