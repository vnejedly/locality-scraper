Here is the URL pattern for numbered pages about geological localities(`page_number`=1..3968):

`https://lokality.geology.cz/<number>`

I want to scrape all pages content, creating a separate directory `<project_root>/localities/<page_name>` for each of them. The `page_name` will be constructed as a 4-digit number of the page containg leading zeros folowed by the casefolded title of the page transliterated from the czech diacritic into ASCII and stripped of all characters other than letters and separating words by "-" and maintaining the pattern:

0003-vate-pisky
0007-spility-u-klicavske-prehrady
0147-soutice-piskovna
2536-zlaty-potok
...

Each page directory must contain an `.md` file called `content.md` containing the textual content of the page (resembling the original formatting in the `md` style).

Every page also contains a frame with map view. This map view must be saved as an image into the page directory in an appropriate mime format (you consider the most appropriate one) under the name pattern `locality-map.<mime-extension>`.

Each page can contain also a set of images in the right column of the page just under the title "Fotoarchiv". Each of those images must be downloaded and saved into a fsubfolder called `images` under its original filename. As each of the images has a short textual description under it, store this description into the exif data of the image.

Create the python script for scraping the pages, save it into the project directory and run it finally. The script should also continuously write out the actually downloaded `page_name`.
