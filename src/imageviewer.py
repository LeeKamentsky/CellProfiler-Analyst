from dbconnect import *
from datamodel import DataModel
from imagecontrolpanel import *
from imagepanel import ImagePanel
from properties import Properties
import imagetools
import cPickle
import logging
import numpy as np
import wx

p = Properties.getInstance()
db = DBConnect.getInstance()

REQUIRED_PROPERTIES = ['image_channel_colors', 'object_name', 'image_names', 'image_id']

CL_NUMBERED = 'numbered'
CL_COLORED = 'colored'
ID_SELECT_ALL = wx.NewId()
ID_DESELECT_ALL = wx.NewId()

def get_classifier_window():
    from classifier import ID_CLASSIFIER
    win = wx.FindWindowById(ID_CLASSIFIER)
    if win:
        return win
    wins = [x for x in wx.GetTopLevelWindows() if x.Name=='Classifier']
    if wins:
        return wins[0]
    else:
        return None
    
    
def rescale_image_coord_to_display(x, y):
    ''' Rescale coordinate to fit the rescaled image dimensions '''
    if not p.rescale_object_coords:
        return x,y
    x = x * p.image_rescale[0] / p.image_rescale_from[0]
    y = y * p.image_rescale[1] / p.image_rescale_from[1]
    return x,y

def rescale_display_coord_to_image(x, y):
    ''' Rescale coordinate to fit the display image dimensions '''
    if not p.rescale_object_coords:
        return x,y
    x = x * p.image_rescale_from[0] / p.image_rescale[0]
    y = y * p.image_rescale_from[1] / p.image_rescale[1]
    return x,y

class ImageViewerPanel(ImagePanel):
    '''
    ImagePanel with selection and object class labels. 
    '''
    def __init__(self, imgs, chMap, img_key, parent, scale=1.0, brightness=1.0, contrast=None):
        super(ImageViewerPanel, self).__init__(imgs, chMap, parent, scale, brightness, contrast=contrast)
        self.selectedPoints = []
        self.classes        = {}  # {'Positive':[(x,y),..], 'Negative': [(x2,y2),..],..}
        self.classVisible   = {}
        self.class_rep      = CL_COLORED
        self.img_key        = img_key
        self.show_object_numbers = False
    
    def OnPaint(self, evt):
        dc = super(ImageViewerPanel, self).OnPaint(evt)
        font = self.GetFont()
        font.SetPixelSize((6,12))
        dc.SetFont(font)
        dc.SetTextForeground('WHITE')

        # Draw object numbers
        if self.show_object_numbers:
            dc.SetLogicalFunction(wx.XOR)
            dc.BeginDrawing()
            for i, (x,y) in enumerate(self.ob_coords):
                x = x * self.scale - 6*(len('%s'%i)-1)
                y = y * self.scale - 6
                dc.DrawText('%s'%(i + 1), x, y)
            dc.EndDrawing()

        # Draw class numbers over each object
        if self.classes:
            for (name, cl), clnum, color in zip(self.classes.items(), self.class_nums, self.colors):
                if self.classVisible[name]:
                    dc.BeginDrawing()
                    for (x,y) in cl:
                        if self.class_rep==CL_NUMBERED:
                            dc.SetLogicalFunction(wx.XOR)
                            x = x * self.scale - 3
                            y = y * self.scale - 6
                            dc.DrawText(clnum, x, y)
                        else:
                            x = x * self.scale - 2
                            y = y * self.scale - 2
                            w = h = 4
                            dc.SetPen(wx.Pen(color,1))
                            dc.SetBrush(wx.Brush(color, style=wx.TRANSPARENT))
                            dc.DrawRectangle(x,y,w,h)
                            dc.DrawRectangle(x-1,y-1,6,6)
                    dc.EndDrawing()
                    
        # Draw small white (XOR) boxes at each selected point
        dc.SetLogicalFunction(wx.XOR)
        dc.SetPen(wx.Pen("WHITE",1))
        dc.SetBrush(wx.Brush("WHITE", style=wx.TRANSPARENT))
        for (x,y) in self.selectedPoints:
            x = x * self.scale - 3
            y = y * self.scale - 3
            w = h = 6
            dc.BeginDrawing()
            dc.DrawRectangle(x,y,w,h)
            dc.EndDrawing()
        return dc
    
    def SetSelectedPoints(self, posns):
        self.selectedPoints = posns
        self.Refresh()
                        
    def SelectPoint(self, pos):
        self.selectedPoints += [pos]
        self.Refresh()
        
    def DeselectPoint(self, pos):
        self.selectedPoints.remove(pos)
        self.Refresh()
        
    def TogglePointSelection(self, pos):
        if pos in self.selectedPoints:
            self.DeselectPoint(pos)
        else:
            self.SelectPoint(pos)
        
    def DeselectAll(self):
        self.selectedPoints = []
        self.Refresh()

    def SetClassPoints(self, classes):
        if len(classes) == 0:
            logging.warn('There are no objects to classify in this image.')
            return
        from matplotlib.pyplot import cm
        self.classes = classes
        vals = np.arange(0, 1, 1. / len(classes))
        vals += (1.0 - vals[-1]) / 2
        self.colors = [np.array(cm.jet(val)) * 255 for val in vals]
        self.class_nums = [str(i+1) for i,_ in enumerate(classes)]

        self.classVisible = {}
        for className in classes.keys():
            self.classVisible[className] = True 
        self.Refresh()
        
    def ToggleObjectNumbers(self):
        self.show_object_numbers = not self.show_object_numbers
        if self.show_object_numbers:
            self.ob_coords = db.GetAllObjectCoordsFromImage(self.img_key)
        self.Refresh()
        
    def ToggleClassRepresentation(self):
        if self.class_rep==CL_NUMBERED:
            self.class_rep = CL_COLORED
        else:
            self.class_rep = CL_NUMBERED
        self.Refresh()
        
    def ToggleClass(self, className, show):
        self.classVisible[className] = show
        self.Refresh()


class ImageViewer(wx.Frame):
    '''
    A frame that takes a list of np arrays representing image channels 
    and merges and displays them as a single image.
    Menus are provided to change the RGB mapping of each channel passed in.
    Note: chMap is passed by reference by default, this means that the caller
       of ImageViewer can have it's own chMap (if any) updated by changes
       made in the viewer.  Otherwise pass in a copy.
    '''
    def __init__(self, imgs=None, chMap=None, img_key=None, parent=None, title='Image Viewer', 
                 classifier=None, brightness=1.0, scale=1.0, contrast=None, 
                 classCoords=None):
        '''
        imgs  : [np.array(dtype=float32), ... ]
        chMap : ['color', ...]
            defines the colors that will be mapped to the corresponding
            image channels in imgs
        img_key : key for this image in the database, to allow selection of cells
        NOTE: imgs lists must be of the same length.
        '''
        wx.Frame.__init__(self, parent, -1, title)
        self.SetName('ImageViewer')
        
        self.img_key     = img_key
        self.classifier  = parent
        self.sw          = wx.ScrolledWindow(self)
        self.selection   = []
        self.maxSize     = tuple([xy-50 for xy in wx.DisplaySize()])
        self.defaultFile = 'MyImage.png'
        self.defaultPath = ''
        self.imagePanel  = None
        self.cp          = None
        self.controls    = None
        self.first_layout = True
        if chMap is None:
            try:
                chMap = p.image_channel_colors
            except:
                pass

        self.SetSizer(wx.BoxSizer(wx.VERTICAL))
        self.CreateMenus()
        self.CreatePopupMenu()
        if imgs and chMap:
            self.SetImage(imgs, chMap, brightness, scale, contrast)
        else:
            self.OnOpenImage()
        self.DoLayout()
        self.Center()
        
        if classCoords is not None:
            self.SetClasses(classCoords)
            
    def AutoTitle(self):
        if p.plate_id and p.well_id:
            plate, well = db.execute('SELECT %s, %s FROM %s WHERE %s'%(p.plate_id, p.well_id, p.image_table, GetWhereClauseForImages([self.img_key])))[0]
            title = '%s %s, %s %s, image-key %s'%(p.plate_id, plate, p.well_id, well, str(self.img_key))
        else:
            title = 'image-key %s'%(str(self.img_key))
        self.SetTitle(title)
        
    def CreatePopupMenu(self):
        self.popupMenu = wx.Menu()
        self.sel_all = wx.MenuItem(self.popupMenu, ID_SELECT_ALL, 'Select all\tCtrl+A')
        self.deselect = wx.MenuItem(self.popupMenu, ID_DESELECT_ALL, 'Deselect all\tCtrl+D')
        self.popupMenu.AppendItem(self.sel_all)
        self.popupMenu.AppendItem(self.deselect)
        accelerator_table = wx.AcceleratorTable([(wx.ACCEL_CMD,ord('A'),ID_SELECT_ALL),
                                                 (wx.ACCEL_CMD,ord('D'),ID_DESELECT_ALL),])
        self.SetAcceleratorTable(accelerator_table)

    def SetImage(self, imgs, chMap=None, brightness=1, scale=1, contrast=None):
        self.AutoTitle()
        self.chMap = chMap or p.image_channel_colors
        self.toggleChMap = self.chMap[:]
        if self.imagePanel:
            self.imagePanel.Destroy()
        self.imagePanel = ImageViewerPanel(imgs, self.chMap, self.img_key, 
                                           self.sw, brightness=brightness, 
                                           scale=scale, contrast=contrast)
        self.imagePanel.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)
        self.imagePanel.Bind(wx.EVT_SIZE, self.OnResizeImagePanel)
        self.imagePanel.Bind(wx.EVT_RIGHT_DOWN, self.OnRightDown)

    def CreateMenus(self):
        self.SetMenuBar(wx.MenuBar())
        # File Menu
        self.fileMenu = wx.Menu()
        self.openImageMenuItem = self.fileMenu.Append(-1, text='Open Image\tCtrl+O')
        self.saveImageMenuItem = self.fileMenu.Append(-1, text='Save Image\tCtrl+S')
        self.fileMenu.AppendSeparator()
        self.exitMenuItem      = self.fileMenu.Append(-1, text='Exit\tCtrl+Q')
        self.GetMenuBar().Append(self.fileMenu, 'File')
        # Classify menu (requires classifier window
        self.classifyMenu = wx.Menu()
        self.classifyMenuItem = self.classifyMenu.Append(-1, text='Classify Image')
        self.GetMenuBar().Append(self.classifyMenu, 'Classify')
        # View Menu
        self.viewMenu = wx.Menu()
        self.objectNumberMenuItem = self.viewMenu.Append(-1, text='Show %s numbers\tCtrl+`'%p.object_name[0])
        self.classViewMenuItem = self.viewMenu.Append(-1, text='View %s classes as numbers'%p.object_name[0])
        self.GetMenuBar().Append(self.viewMenu, 'View')
    
    def CreateChannelMenus(self):
        ''' Create color-selection menus for each channel. '''
        # clean up existing channel menus
        try:
            menus = set([items[2].Menu for items in self.chMapById.values()])
            for menu in menus:
                for i, mbmenu in enumerate(self.MenuBar.Menus):
                    if mbmenu[0] == menu:
                        self.MenuBar.Remove(i)
            for menu in menus:
                menu.Destroy()
        except:
            pass
        
        chIndex = 0
        self.chMapById = {}
        channel_names = []
        for i, chans in enumerate(p.channels_per_image):
            chans = int(chans)
            # Construct channel names, for RGB images, append a # to the end of
            # each channel. 
            name = p.image_names[i]
            if chans == 1:
                channel_names += [name]
            elif chans == 3: #RGB
                channel_names += ['%s [%s]'%(name,x) for x in 'RGB']
            else:
                raise ValueError('Unsupported number of channels (%s) specified in properties field channels_per_image.'%(chans))
        
        for channel, setColor in zip(channel_names, self.chMap):
            channel_menu = wx.Menu()
            for color in ['Red', 'Green', 'Blue', 'Cyan', 'Magenta', 'Yellow', 'Gray', 'None']:
                id = wx.NewId()
                item = channel_menu.AppendRadioItem(id,color)
                self.chMapById[id] = (chIndex, color, item, channel_menu)
                if color.lower() == setColor.lower():
                    item.Check()
                self.Bind(wx.EVT_MENU, self.OnMapChannels, item)
            self.GetMenuBar().Append(channel_menu, channel)
            chIndex+=1
                                
    def DoLayout(self):
        if self.imagePanel:
            if not self.cp:
                self.cp = wx.CollapsiblePane(self, label='Show controls', style=wx.CP_DEFAULT_STYLE|wx.CP_NO_TLW_RESIZE)
                self.controls  = ImageControlPanel(self.cp.GetPane(), self.imagePanel, 
                                                   brightness=self.imagePanel.brightness,
                                                   scale=self.imagePanel.scale, 
                                                   contrast=self.imagePanel.contrast)
                self.Bind(wx.EVT_COLLAPSIBLEPANE_CHANGED, self.OnPaneChanged, self.cp)
            else:
                self.controls.SetListener(self.imagePanel)
            self.Sizer.Clear()
            self.Sizer.Add(self.sw, proportion=1, flag=wx.EXPAND)
            self.Sizer.Add(self.cp, 0, wx.RIGHT|wx.LEFT|wx.EXPAND, 25)
            w, h = self.imagePanel.GetSize()
            if self.first_layout:
                self.SetClientSize( (min(self.maxSize[0], w*self.imagePanel.scale),
                                     min(self.maxSize[1], h*self.imagePanel.scale+55)) )
                self.Center()
                self.first_layout = False
            self.sw.SetScrollbars(1, 1, w*self.imagePanel.scale, h*self.imagePanel.scale)    
            self.CreateChannelMenus()
            # Annoying: Need to bind 3 windows to KEY_UP in case focus changes.
            self.Bind(wx.EVT_KEY_UP, self.OnKey)
            self.sw.Bind(wx.EVT_KEY_UP, self.OnKey)
            self.cp.Bind(wx.EVT_KEY_UP, self.OnKey)
            self.imagePanel.Bind(wx.EVT_KEY_UP, self.OnKey)
            self.Bind(wx.EVT_MENU, lambda(e):self.SelectAll(), self.sel_all)
            self.Bind(wx.EVT_MENU, lambda(e):self.DeselectAll(), self.deselect)
            
        self.fileMenu.Bind(wx.EVT_MENU_OPEN, self.OnOpenFileMenu)
        self.classifyMenu.Bind(wx.EVT_MENU_OPEN, self.OnOpenClassifyMenu)
        self.viewMenu.Bind(wx.EVT_MENU_OPEN, self.OnOpenViewMenu)
        self.Bind(wx.EVT_MENU, self.OnOpenImage, self.openImageMenuItem)
        self.Bind(wx.EVT_MENU, self.OnSaveImage, self.saveImageMenuItem)
        self.Bind(wx.EVT_MENU, self.OnSaveImage, self.saveImageMenuItem)
        self.Bind(wx.EVT_MENU, self.OnOpenImage, self.openImageMenuItem)
        self.Bind(wx.EVT_MENU, self.OnChangeClassRepresentation, self.classViewMenuItem)
        self.Bind(wx.EVT_MENU, self.OnShowObjectNumbers, self.objectNumberMenuItem)
        self.Bind(wx.EVT_MENU, self.OnClassifyImage, self.classifyMenuItem)
        self.Bind(wx.EVT_MENU, lambda evt:self.Close(), self.exitMenuItem)
        
    def OnClassifyImage(self, evt=None):
        logging.info('Classifying image with key=%s...'%str(self.img_key))
        classifier = get_classifier_window()
        if classifier is None:
            logging.error('Could not find Classifier!')
            return
        # Score the Image
        classHits = classifier.ScoreImage(self.img_key)
        # Get object coordinates in image and display
        classCoords = {}
        for className, obKeys in classHits.items():
            classCoords[className] = [db.GetObjectCoords(key) for key in obKeys]
        self.SetClasses(classCoords)

    def OnPaneChanged(self, evt=None):
        self.Layout()
        if self.cp.IsExpanded():
            self.cp.SetLabel('Hide controls')
        else:
            self.cp.SetLabel('Show controls')

    def OnMapChannels(self, evt):
        if evt.GetId() in self.chMapById.keys():
            (chIdx,color,_,_) = self.chMapById[evt.GetId()]
            self.chMap[chIdx] = color
            if color.lower() != 'none':
                self.toggleChMap[chIdx] = color
            self.MapChannels(self.chMap)

    def MapChannels(self, chMap):
        self.chMap = chMap
        self.imagePanel.MapChannels(chMap)

    def OnKey(self, evt):
        ''' Keyboard shortcuts '''
        keycode = evt.GetKeyCode()
        chIdx = keycode-49
        if evt.CmdDown() or evt.ControlDown():
            if keycode == ord('A'):
                self.SelectAll()
            elif keycode == ord('D'):
                self.DeselectAll()
            elif keycode == ord('J'):
                self.imagePanel.SetContrastMode('None')
                self.controls.SetContrastMode('None')
            elif keycode == ord('K'):
                self.imagePanel.SetContrastMode('Linear')
                self.controls.SetContrastMode('Linear')
            elif keycode == ord('L'):
                self.imagePanel.SetContrastMode('Log')
                self.controls.SetContrastMode('Log')
            elif len(self.chMap) > chIdx >= 0:   
                # ctrl+n where n is the nth channel
                self.ToggleChannel(chIdx)
            else:
                evt.Skip()
        else:
            if keycode == ord(' '):
                self.cp.Collapse(not self.cp.IsCollapsed())
                self.OnPaneChanged()
            else:
                evt.Skip()
        
    def OnResizeImagePanel(self, evt):
        self.sw.SetVirtualSize(evt.GetSize())
            
    def ToggleChannel(self, chIdx):
        if self.chMap[chIdx] == 'None':
            for (idx, color, item, menu) in self.chMapById.values():
                if idx == chIdx and color.lower() == self.toggleChMap[chIdx].lower():
                    item.Check()   
            self.chMap[chIdx] = self.toggleChMap[chIdx]
            self.MapChannels(self.chMap)
        else:
            for (idx, color, item, menu) in self.chMapById.values():
                if idx == chIdx and color.lower() == 'none':
                    item.Check()
            self.chMap[chIdx] = 'None'
            self.MapChannels(self.chMap)
            
    def SelectAll(self):
        if p.object_table:
            coords = db.GetAllObjectCoordsFromImage(self.img_key)
            self.selection = db.GetObjectsFromImage(self.img_key)
            if p.rescale_object_coords:
                coords = [rescale_image_coord_to_display(x, y) for (x, y) in coords]
            self.imagePanel.SetSelectedPoints(coords)
        
    def DeselectAll(self):
        self.selection = []
        self.imagePanel.DeselectAll()
        
    def SelectObject(self, obkey):
        coord = db.GetObjectCoords(obkey)
        if p.rescale_object_coords:
            coord = rescale_image_coord_to_display(coord[0], coord[1])
        self.selection += [coord]
        self.imagePanel.SetSelectedPoints([coord])
    
    def SetClasses(self, classCoords):
        self.classViewMenuItem.Enable()
        self.imagePanel.SetClassPoints(classCoords)
        self.controls.SetClassPoints(classCoords)
        self.Refresh()
        self.Layout()
        
    def OnLeftDown(self, evt):
        if self.img_key and p.object_table:
            x = evt.GetPosition().x / self.imagePanel.scale
            y = evt.GetPosition().y / self.imagePanel.scale
            if p.rescale_object_coords:
                x, y = rescale_display_coord_to_image(x, y)
            obKey = db.GetObjectNear(self.img_key, x, y)

            if not obKey: return
            
            # update existing selection
            if not evt.ShiftDown():
                self.selection = [obKey]
                self.imagePanel.DeselectAll()
            else:
                if obKey not in self.selection:
                    self.selection += [obKey]
                else:
                    self.selection.remove(obKey)
            
            # select the object
            (x,y) = db.GetObjectCoords(obKey)            
            if p.rescale_object_coords:
                x, y = rescale_image_coord_to_display(x, y)
            self.imagePanel.TogglePointSelection((x,y))
            
            if self.selection:
                # start drag
                source = wx.DropSource(self)
                # wxPython crashes unless the data object is assigned to a variable.
                data_object = wx.CustomDataObject("ObjectKey")
                data_object.SetData(cPickle.dumps( (self.GetId(), self.selection) ))
                source.SetData(data_object)
                result = source.DoDragDrop(flags=wx.Drag_DefaultMove)
                if result is 0:
                    pass
                
    def OnRightDown(self, evt):
        ''' On right click show popup menu. '''
        self.PopupMenu(self.popupMenu, evt.GetPosition())

    def OnOpenImage(self, evt=None):
        # 1) Get the image key
        # Start with the table_id if there is one
        tblNum = None
        if p.table_id:
            dlg = wx.TextEntryDialog(self, p.table_id+':','Enter '+p.table_id)
            dlg.SetValue('0')
            if dlg.ShowModal() == wx.ID_OK:
                try:
                    tblNum = int(dlg.GetValue())
                except ValueError:
                    errdlg = wx.MessageDialog(self, 'Invalid value for %s!'%(p.table_id), "Invalid value", wx.OK|wx.ICON_EXCLAMATION)
                    errdlg.ShowModal()
                    return
                dlg.Destroy()
            else:
                dlg.Destroy()
                return
        # Then get the image_id
        dlg = wx.TextEntryDialog(self, p.image_id+':','Enter '+p.image_id)
        dlg.SetValue('')
        if dlg.ShowModal() == wx.ID_OK:
            try:
                imgNum = int(dlg.GetValue())
            except ValueError:
                errdlg = wx.MessageDialog(self, 'Invalid value for %s!'%(p.image_id), "Invalid value", wx.OK|wx.ICON_EXCLAMATION)
                errdlg.ShowModal()
                return
            dlg.Destroy()
        else:
            dlg.Destroy()
            return
        # Build the imkey
        if p.table_id:
            imkey = (tblNum,imgNum)
        else:
            imkey = (imgNum,)
            
        dm = DataModel.getInstance()
        if imkey not in dm.GetAllImageKeys():
            errdlg = wx.MessageDialog(self, 'There is no image with that key.', "Couldn't find image", wx.OK|wx.ICON_EXCLAMATION)
            errdlg.ShowModal()
            self.Destroy()
        else:            
            # load the image
            self.img_key = imkey
            self.SetImage(imagetools.FetchImage(imkey), p.image_channel_colors)
            self.DoLayout()
            
    def OnSaveImage(self, evt):
        import os
        saveDialog = wx.FileDialog(self, message="Save as:",
                                   defaultDir=self.defaultPath, defaultFile=self.defaultFile,
                                   wildcard='PNG file (*.png)|*.png|JPG file (*.jpg, *.jpeg)|*.jpg', 
                                   style=wx.SAVE|wx.FD_OVERWRITE_PROMPT)
        if saveDialog.ShowModal()==wx.ID_OK:
            filename = str(saveDialog.GetPath())
            self.defaultPath, self.defaultFile = os.path.split(filename)
            format = os.path.splitext(filename)[-1]
            saveDialog.Destroy()
            if not format.upper() in ['.PNG','.JPG','.JPEG']:
                errdlg = wx.MessageDialog(self, 'Invalid file extension (%s)! File extension must be .PNG or .JPG.'%(format),
                                          "Invalid file extension", wx.OK|wx.ICON_EXCLAMATION)
                if errdlg.ShowModal() == wx.ID_OK:
                    return self.OnSaveImage(evt)
            if format.upper()=='.JPG':
                format = '.JPEG'
            imagetools.SaveBitmap(self.imagePanel.bitmap, filename, format.upper()[1:])


    def OnChangeClassRepresentation(self, evt):
        if self.classViewMenuItem.Text.endswith('numbers'):
            self.classViewMenuItem.Text = 'View %s classes as colors'%p.object_name[0]
        else:
            self.classViewMenuItem.Text = 'View %s classes as numbers'%p.object_name[0]
        self.imagePanel.ToggleClassRepresentation()
        
    def OnShowObjectNumbers(self, evt):
        if self.objectNumberMenuItem.Text.startswith('Hide'):
            self.objectNumberMenuItem.Text = 'Show %s numbers\tCtrl+`'%(p.object_name[0])
        else:
            self.objectNumberMenuItem.Text = 'Hide %s numbers\tCtrl+`'%(p.object_name[0])
        self.imagePanel.ToggleObjectNumbers()
        
    def OnOpenFileMenu(self, evt=None):
        if self.imagePanel:
            self.saveImageMenuItem.Enable()
        else:
            self.saveImageMenuItem.Enable(False)
        
    def OnOpenViewMenu(self, evt=None):
        if self.imagePanel and self.imagePanel.classes:
            self.classViewMenuItem.Enable()
        else:
            self.classViewMenuItem.Enable(False)
    
    def OnOpenClassifyMenu(self, evt=None):
        classifier = get_classifier_window()
        if classifier and classifier.IsTrained():
            self.classifyMenuItem.Enable()
        else:
            self.classifyMenuItem.Enable(False)



if __name__ == "__main__":
    logging.basicConfig()
    
#    p.LoadFile('/Users/afraser/Desktop/cpa_example/example.properties')
#    p.LoadFile('../properties/nirht_test.properties')
#    p.LoadFile('../properties/2008_07_29_Giemsa.properties')
    app = wx.PySimpleApp()
    from datamodel import DataModel
    import imagetools
    from imagereader import ImageReader
    
    p = Properties.getInstance()
    p.image_channel_colors = ['red','green','blue']
    p.object_name = ['cell', 'cells']
    p.image_names = ['a', 'b', 'c']
    p.image_id = 'ImageNumber'
    p.channels_per_image = [1,1,1]
    images = [np.ones((200,200)),
              np.ones((200,200)) / 2. ,
              np.ones((200,200)) / 4. ,
              np.ones((200,200)) / 8. ,
              np.ones((200,200)),
              np.ones((200,200)) / 2. ,
              np.ones((200,200)) / 4. ,
              np.ones((200,200)) / 8. ,
              ]

    pixels = []
    for channel in p.image_channel_colors:        
        pixels += [imagetools.tile_images(images)]
    
    f = ImageViewer(pixels)
    f.Show()
    

##    if not p.show_load_dialog():
##        logging.error('ImageViewer requires a properties file.  Exiting.')
##        wx.GetApp().Exit()
##        raise Exception('ImageViewer requires a properties file.  Exiting.')
##    
##    db = DBConnect.getInstance()
##    dm = DataModel.getInstance()
##    ir = ImageReader()
##    
##    obKey = dm.GetRandomObject()
##    imagetools.ShowImage(obKey[:-1], p.image_channel_colors, None)
#    filenames = db.GetFullChannelPathsForImage(obKey[:-1])
#    images = ir.ReadImages(filenames)
#    frame = ImageViewer(imgs=images, chMap=p.image_channel_colors, img_key=obKey[:-1])
#    frame.Show()
    
#    for i in xrange(1):
#        obKey = dm.GetRandomObject()
#        imgs = imagetools.FetchImage(obKey[:-1])
#        f2 = ImageViewer(imgs=imgs, img_key=obKey[:-1], chMap=p.image_channel_colors, title=str(obKey[:-1]))
#        f2.Show(True)
    
#    classCoords = {'a':[(100,100),(200,200)],'b':[(200,100),(200,300)] }
#    f2.SetClasses(classCoords)
    
    #imagetools.SaveBitmap(frame.imagePanel.bitmap, '/Users/afraser/Desktop/TEST.png')
           
    app.MainLoop()
