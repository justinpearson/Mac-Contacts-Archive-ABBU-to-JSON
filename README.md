
# Mac Address Book ABBU file to JSON

- Justin Pearson
- Nov 22, 2022

This python script converts a Apple "Contacts Archive" (eg, `My Contacts.abbu`) into a nice JSON format.
It also extracts images stored in the .abbu file.

# Motivation

In Apple's "Contacts" app, File > Export lets you export your contacts as a nice readable vCard format (`.vcf`) or as a strange "Contacts Archive" (`.abbu`) file format. The .vcf file doesn't contain images, so you'll lose the pictures of each contact. In contrast, the .abbu file contains the images (right click, "Show Package Contents", find "Images" directory), but there's no way to view the abbu file in Contacts App without wiping out your existing contacts!:

```
Are you sure you want to replace your Contacts data
with the contacts in "Contacts - 2012-MACBOOK.abbu"?

If you click Replace All, the information in this Contacts archive
completely replaces your Contacts data. You can’t undo this action.
```

![](assets/import-abbu-warning.png)

[Searching Apple StackExchange](https://apple.stackexchange.com/search?q=abbu+file) and [GitHub](https://github.com/search?q=%22abbu%22&type=repositories) shows that lots of folks have this problem:

- Github user `jonpacker` wrote an abbu converter ([link](https://github.com/jonpacker/abbu-to-adr/blob/master/ocontacts.coffee)), but it's from 2011, it's written in CoffeeScript, it refers to `abcdmr` files (which my abbu file doesn't have), and most importantly, it misses important contact data: It imports "person" files `.abcdp`, but in my experience, the SQLite db (`.abcddb`) has more contact info than what's in the `abcdp` files: I had one abbu file with 100 contacts in the SQLite db, but only 20 `.abcdp` files.

- Annoyingly, on StackExchange, a [common solution](https://apple.stackexchange.com/a/49172/145895) has you enable iCloud, upload your current contacts, disable your network connection, then import the abbu file (thereby wiping out your contacts) to view it or export it, then reverse the process to recover your "real" contacts from iCloud. Ugh! Maybe that works, but enabling iCloud has privacy issues, and also it seems risky to depend on iCloud's file-sync logic.

- [Another solution](https://apple.stackexchange.com/questions/30544/is-a-partial-restore-from-an-address-book-abbu-file-possible) has you create a guest account on your Mac and import the Contacts there. That solution didn't work for me: Contacts claimed to import it, but no contacts appeared in the app. [One commenter](https://apple.stackexchange.com/questions/30544/is-a-partial-restore-from-an-address-book-abbu-file-possible#comment135098_30545) with the same issue blamed this on Apple changing the .abbu file format between MacOS Lion and Mavericks.

- [The best solution](https://apple.stackexchange.com/a/209942/145895) uses a SQLite database browser to view the SQLite db `AddressBook-v22.abcddb` contained in the .abbu file (right click, "Show Package Contents"). This solution gives a short SQL query to extract some basic contact info from the abbu file. But you lose the contact images.

I wrote this Python program that extends ^-- that solution:

- It queries the SQLite db to get the contact info, automatically finding and joining relevant tables.
- It copies the images out of various Images/ directories, giving them appropriate file extensions.
- It renames the images to include the names of the contacts, not just the UIDs.


# Usage

Put a .abbu file in the `in/` directory and run this script.
This script parses the contacts and images in the .abbu file,
and creates 3 kinds of outputs in the `out/` directory:

1. `contacts.json` contains the contacts, formatted nicely:
   ```
      {
          "uid": "C13384AC-D081-4190-B5CB-DAEEE889A64D",
          "organization": "Apple Inc.",
          "phone": [
              [
                  "Main",
                  "1-800-MY-APPLE"
              ],
              [
                  "Office",
                  "123-123-1234"
              ]
          ],
          "url": [
              [
                  "HomePage",
                  "http://www.apple.com"
              ]
          ],
          "address": [
              [
                  "Work",
                  {
                      "street": "1 Infinite Loop",
                      "city": "Cupertino",
                      "state": "CA",
                      "zip": "95014",
                      "country": "United States",
                      "country code": "us"
                  }
              ]
          ],
          "ims": [
              {
                  "path": "/foo/in/My-Contacts.abbu/Images/C13384AC-D081-4190-B5CB-DAEEE889A64D",
                  "info": "TIFF image data, big-endian, direntries=14, height=320, bps=6, compression=none, PhotometricIntepretation=RGB, orientation=upper-left, width=320\n",
                  "image type": "tiff",
                  "base name": "C13384AC-D081-4190-B5CB-DAEEE889A64D",
                  "dst": "/foo/out/ims/Apple-Inc__C13384AC-D081-4190-B5CB-DAEEE889A64D.tiff"
              }
          ]
      }, ...
      ```
  - Note: the email / address / url / phone fields may have multiple "types", eg, home, work, etc.
  - Note: I preserve the UID in case you need it, like for matching up images with contacts.
  - Note: This json format supports the case where 1 contact has multiple images in the .abbu file. I don't know why an .abbu file has multiple images for some contacts, but it does.

2. The `ims/` directory contains copies of images that were found in the abbu file.
   To make the image filenames easier to use, I prepend the first name, last name, and organization.
   Also I add a file extension.

3. The `ims/orphans/` directory contains copies of images that were found in the abbu file,
   but whose filenames (UIDs) don't map to any UID of any contact.




# Details

An Apple "Contacts Archive" is a directory like `foo.abbu`. I think the `ab` stands for "Address Book". It stores contacts and their images in 3 main ways:

1. Contacts (first name, last name, phone number, etc) are stored in a SQLite3 db named `foo.abbu/AddressBook-v22.abcddb`.
  - The main table is `ZABCDRECORD` and other tables like `ZABCDEMAILADDRESS` have a column `ZOWNER` or `ZCONTACT` that's a foreign key to `ZABCDRECORD.Z_PK`. 
    - My script automatically joins to `ZABCDRECORD` any table with a `ZOWNER` or `ZCONTACT` column, to try to extract all relevant info from the SQLite db.
	- For my initial exploration, I used [DB Browser for SQLite.app](https://sqlitebrowser.org/) to browse this db, and I had to copy the db into a safe location in my home folder, not in `~/Library/`, to avoid file-permissions errors with `~/Library`.

2. Contact info also appears in Apple binary plist files with extension `.abcdp`, e.g., `foo.abbu/Metadata/95693224-BE9F-4E3C-8D13-86CDF31FF941:ABPerson.abcdp`.
  - See the contents of the `.abcdp` file with `plutil -p 'foo.abbu/Metadata/C34E458D-9B42-4818-8E27-64B4D0B41540:ABPerson.abcdp'`
  - Each `.abcdp` file contains one person's info.
  - In my experience, every `.abcdp` file seems to corresponds to 1 person in the db, and the .abcdp contains a subset of what's in the SQLite db for that person.
    - So you could probably safely ignore the `.abcdp` files. But to be safe, my script imports them and verifies all their info is already stored in the db-based contacts.
  - Importantly, not all contacts in the db have a .abcdp file. So if you simply use `plutil` to extract the `.abcdp` files, like [this answer](https://apple.stackexchange.com/a/223875/145895) says, you'll miss many contacts!


3. Images are stored in `foo.abbu/Images/`, as files with or without file extensions, named after their UID in the SQLite db.
  - Image files may not have file extensions.
    - When my script copies an image into `out/`, it appends the file extension for easy viewing.
  - Image files are not just jpgs, but also tiffs.
  - The `foo.abbu` Contact Archive may not have an `Images/` directory.
  - Not all contacts in the SQLite db have images.
  - For some reason, there seem to be a lot of image files with different UIDs, but it's all the same the picture (it's the same person). Sometimes the image is cropped slightly differently. Weird.
  - Lots of images' UIDs don't actually appear in the DB. It's like a contact was deleted out of the DB but its corresponding image wasn't deleted.


- Lastly, `foo.abbu` sometimes contains a `Sources/` dir that seems to contain other `.abcddb` sqlite dbs, other `.abcdp` files, and other `Images/` directories. My program finds and parses them all. So there may be duplicate contacts.


# Warnings / Future Work

- There's some sort of "Group" concept in a .abbu file, which I ignore.
- If you run this program multiple times, you should delete the images out of `ims/` and `ims/orphans/`,
or else you'll end up with image copies like `foo__2.jpg`, `foo__3.jpg`, etc. This is because my program
tries not to overwrite image files as it copies them out of the .abbu file.
